// analytics.js — REST endpoints. The math lives in SQL window functions
// (percentile, rank, HHI) — the same logic Power BI would have needed DAX for.
import { Router } from 'express';
import { query } from '../db.js';

const r = Router();

// List leagues (for the dashboard's league selector)
r.get('/leagues', async (_req, res, next) => {
  try {
    res.json(await query(`
      SELECT league_id, league_name, season, number_of_teams,
             is_superflex, te_premium_value
      FROM dim_leagues
      ORDER BY league_name, season DESC
    `));
  } catch (e) { next(e); }
});

// League diagnostics: each team's total value, within-league percentile + rank,
// and HHI concentration. NOTE the 1.0* float casts — without them SQLite
// integer-divides the value shares to zero and HHI silently breaks.
r.get('/leagues/:id/diagnostics', async (req, res, next) => {
  try {
    const sql = `
      WITH rp AS (
        SELECT league_id, roster_id, fp_market_value AS v
        FROM v_player_market
        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM v_player_market)
          AND league_id = ?
          AND fp_market_value IS NOT NULL
      ),
      rt AS (
        SELECT league_id, roster_id, SUM(v) AS team_value, COUNT(*) AS n_assets
        FROM rp GROUP BY league_id, roster_id
      ),
      hhi AS (
        SELECT rp.league_id, rp.roster_id,
               SUM( (1.0 * rp.v / rt.team_value) * (1.0 * rp.v / rt.team_value) ) AS hhi
        FROM rp JOIN rt ON rt.league_id = rp.league_id AND rt.roster_id = rp.roster_id
        GROUP BY rp.league_id, rp.roster_id
      )
      SELECT m.owner_name,
             rt.roster_id,
             rt.team_value,
             rt.n_assets,
             PERCENT_RANK() OVER (PARTITION BY rt.league_id ORDER BY rt.team_value) AS value_percentile,
             RANK()         OVER (PARTITION BY rt.league_id ORDER BY rt.team_value DESC) AS value_rank,
             h.hhi
      FROM rt
      JOIN hhi h ON h.league_id = rt.league_id AND h.roster_id = rt.roster_id
      LEFT JOIN dim_managers m ON m.league_id = rt.league_id AND m.roster_id = rt.roster_id
      ORDER BY value_rank
    `;
    res.json(await query(sql, [req.params.id]));
  } catch (e) { next(e); }
});

// Arbitrage signals: where FantasyPros and FantasyCalc disagree most (Buy/Sell).
r.get('/leagues/:id/arbitrage', async (req, res, next) => {
  try {
    const sql = `
      SELECT player_name, position, fp_market_value, fc_market_value,
             arb_delta_fp_minus_fc
      FROM v_player_market
      WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM v_player_market)
        AND league_id = ?
        AND fp_market_value IS NOT NULL AND fc_market_value IS NOT NULL
      ORDER BY ABS(arb_delta_fp_minus_fc) DESC
      LIMIT 25
    `;
    res.json(await query(sql, [req.params.id]));
  } catch (e) { next(e); }
});

const MISSING_REL = /no such (table|view)|does not exist|relation .* does not exist/i;

// Three-source value triangulation: expert (FP) vs market (FC) vs production (VBD).
// Off-diagonal players are the signal — what a player PRODUCES (win-now) vs what the
// dynasty market PAYS (future). Returns [] gracefully if points_model.py hasn't run.
r.get('/leagues/:id/value', async (req, res, next) => {
  try {
    const sql = `
      SELECT player_name, position, age,
             fp_market_value, fc_market_value, vbd_value, ppg, vorp
      FROM v_player_value
      WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM v_player_value)
        AND league_id = ?
        AND vbd_value IS NOT NULL AND fp_market_value IS NOT NULL
      ORDER BY fp_market_value DESC
    `;
    res.json(await query(sql, [req.params.id]));
  } catch (e) {
    if (MISSING_REL.test(String(e.message))) return res.json([]); // VBD layer not built yet
    next(e);
  }
});

// Roster drill-down: one team's full asset list with values, age, arbitrage, and
// (if available) production-grounded VBD/PPG. Falls back to the market-only query
// when the production table is absent, so the panel never breaks.
r.get('/leagues/:id/rosters/:rosterId', async (req, res, next) => {
  const params = [req.params.id, Number(req.params.rosterId)];
  const enriched = `
    SELECT m.player_name, m.position, m.age, m.nfl_team,
           m.fp_market_value, m.fc_market_value, m.fp_ecr_2qb, m.fc_trend_30day,
           m.arb_delta_fp_minus_fc, pv.ppg, pv.vbd_value
    FROM v_player_market m
    LEFT JOIN player_production_value pv
      ON pv.league_id = m.league_id AND pv.player_id = m.player_id
     AND pv.season = (SELECT MAX(season) FROM player_production_value)
    WHERE m.snapshot_date = (SELECT MAX(snapshot_date) FROM v_player_market)
      AND m.league_id = ? AND m.roster_id = ?
    ORDER BY (m.fp_market_value IS NULL), m.fp_market_value DESC`;
  const base = `
    SELECT player_name, position, age, nfl_team,
           fp_market_value, fc_market_value, fp_ecr_2qb, fc_trend_30day,
           arb_delta_fp_minus_fc
    FROM v_player_market
    WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM v_player_market)
      AND league_id = ? AND roster_id = ?
    ORDER BY (fp_market_value IS NULL), fp_market_value DESC`;
  try {
    res.json(await query(enriched, params));
  } catch (e) {
    if (MISSING_REL.test(String(e.message))) {
      try { return res.json(await query(base, params)); } catch (e2) { return next(e2); }
    }
    next(e);
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// PASTE INTO: server/src/routes/analytics.js — next to the existing routes,
// ABOVE the `export default r;` line.
// Matches your file's actual handles: router is `r`, query helper is `query`
// from ../db.js, called as `await query(sql, [params])`.
// Placeholders are `?` like your SQLite default; if you run Postgres via
// DATABASE_URL and your other routes use $1/$2, switch them the same way.
//
// VERIFIED against the live warehouse (v_player_value):
//   pooled:      14 Drew rosters, client-side shares sum to 1.0
//   ?by=position: positional cut works — e.g. one roster holds 23.6% of
//                 league RB VBD (the roadmap's "cornering" diagnostic)
// ─────────────────────────────────────────────────────────────────────────────

// Per-roster production (VBD = points over replacement), latest snapshot.
// Default: pooled across positions (the team-dominance number on the KPI card).
// ?by=position: per-position rows — the endpoint the future cornering panel
// reads (share of each position's league-wide VBD).
r.get('/leagues/:leagueId/production', async (req, res) => {
  const lid = req.params.leagueId;
  const byPos = req.query.by === 'position';
  const rows = await query(
    `SELECT v.roster_id${byPos ? ', v.position' : ''},
            SUM(v.vbd_value)       AS production_vbd,
            SUM(v.fp_market_value) AS team_value
     FROM v_player_value v
     WHERE v.league_id = ?
       AND v.snapshot_date = (
         SELECT MAX(snapshot_date) FROM v_player_value WHERE league_id = ?
       )
     GROUP BY v.roster_id${byPos ? ', v.position' : ''}
     ORDER BY v.roster_id`,
    [lid, lid]
  );
  res.json(rows);
});

// ─────────────────────────────────────────────────────────────────────────────
// PASTE INTO: server/src/routes/analytics.js — above `export default r;`
// Matches your handles: r.get + await query(sql, [params]), ? placeholders.
// Serves Step 2's lineup-solver output. SQL verified against the warehouse
// (Drew: 14 rows, OSL 177.25 top). Run lineup_solver.py first or rows are [].
// ─────────────────────────────────────────────────────────────────────────────
 
// Roster-construction summary: Optimal Starting Lineup points (Hungarian
// assignment), surplus startable capital (lost the assignment AND VORP>0),
// and the greedy-vs-optimal gain. points_basis says what scored the lineup
// (realized REG-only ppg until the projection layer lands).
r.get('/leagues/:leagueId/construction', async (req, res) => {
  const lid = req.params.leagueId;
  const rows = await query(
    `SELECT c.roster_id, c.osl_points, c.slots_filled, c.slots_empty,
            c.skipped_slots, c.surplus_count, c.surplus_vorp, c.surplus_points,
            c.hungarian_gain, c.points_basis
     FROM roster_construction c
     WHERE c.league_id = ?
       AND c.snapshot_date = (
         SELECT MAX(snapshot_date) FROM roster_construction WHERE league_id = ?
       )
     ORDER BY c.osl_points DESC`,
    [lid, lid]
  );
  res.json(rows);
});
 
// Per-roster surplus detail (the trade-capital list behind surplus_count).
r.get('/leagues/:leagueId/rosters/:rosterId/surplus', async (req, res) => {
  const { leagueId, rosterId } = req.params;
  const rows = await query(
    `SELECT s.player_id, s.player_name, s.position, s.points, s.vorp
     FROM roster_surplus s
     WHERE s.league_id = ? AND s.roster_id = ?
       AND s.snapshot_date = (
         SELECT MAX(snapshot_date) FROM roster_surplus WHERE league_id = ?
       )
     ORDER BY s.vorp DESC`,
    [leagueId, rosterId, leagueId]
  );
  res.json(rows);
});

export default r;
