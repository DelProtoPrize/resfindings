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

// ─────────────────────────────────────────────────────────────────────────────
// REPLACES the existing GET /leagues/:leagueId/value route in
// server/src/routes/analytics.js. Two changes vs the old route:
//   1. fc_market_value is served — the Win-Now scatter's x-axis moves to
//      FantasyCalc (the locked rule: market metrics on FC; FP is a
//      deterministic exponential of ordinal ranks).
//   2. years_exp (LEFT JOIN dim_players) — rookie designation downstream.
// Columns are a superset of what app.js reads; SQL verified against the
// warehouse (Drew: 315 rows, FC populated 310, top-FC ordering sane).
// ─────────────────────────────────────────────────────────────────────────────

r.get('/leagues/:leagueId/value', async (req, res) => {
  const lid = req.params.leagueId;
  const rows = await query(
    `SELECT v.player_id, v.player_name, v.position, v.roster_id,
            v.fp_market_value, v.fc_market_value, v.vbd_value,
            v.ppg, v.vorp,
            d.years_exp
     FROM v_player_value v
     LEFT JOIN dim_players d ON d.player_id = v.player_id
     WHERE v.league_id = ?
       AND v.snapshot_date = (
         SELECT MAX(snapshot_date) FROM v_player_value WHERE league_id = ?
       )`,
    [lid, lid]
  );
  res.json(rows);
});

// OPTIONAL one-line companion: if you also want the rookie badge in the
// roster drill table, add `d.years_exp` (with the same LEFT JOIN dim_players)
// to your existing /leagues/:leagueId/rosters/:rosterId route's SELECT.
// app.js renders the badge null-safely either way.

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
// REPLACES the /leagues/:leagueId/production route from server_production_route.txt
// (keep ?by=position behavior; adds ?basis=projected).
// PASTE INTO: server/src/routes/analytics.js, above `export default r;`
// SQL verified against the warehouse: realized 14 rosters sum 1.0;
// projected 14 rosters sum 1.0 (after project_production.py has run).
// ─────────────────────────────────────────────────────────────────────────────
 
// Per-roster production. Two bases, same response shape:
//   default            -> realized REG-only VBD (v_player_value)
//   ?basis=projected   -> m1-projected VBD (player_projected_value).
//      LABEL THAT TRAVELS WITH IT: at preseason as-ofs the projection is
//      statistically indistinguishable from the flat-ECR baseline (Model Lab,
//      m1 verdicts); its CI-clearing edge is in-season. Serve it, never
//      oversell it.
//   ?by=position       -> positional cut (realized basis only for now; the
//      cornering panel's endpoint).
r.get('/leagues/:leagueId/production', async (req, res) => {
  const lid = req.params.leagueId;
  if (req.query.basis === 'projected') {
    const rows = await query(
      `SELECT p.roster_id,
              SUM(p.vbd_proj) AS production_vbd,
              MAX(p.as_of_date) AS as_of_date,
              MAX(p.model_id)  AS model_id
       FROM player_projected_value p
       WHERE p.league_id = ?
         AND p.as_of_date = (
           SELECT MAX(as_of_date) FROM player_projected_value WHERE league_id = ?
         )
       GROUP BY p.roster_id
       ORDER BY p.roster_id`,
      [lid, lid]
    );
    return res.json(rows);
  }
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

// ─────────────────────────────────────────────────────────────────────────────
// PASTE INTO: server/src/routes/analytics.js — above `export default r;`
// Step 4: positional cornering on the FIXED realized replacement bar.
// SQL verified against the warehouse (Drew: 56 roster cells, 4 summary rows).
// Run cornering_metrics.py first or both arrays come back empty (dashes
// downstream — honest empty state, never fabrication).
// ─────────────────────────────────────────────────────────────────────────────

// Positional VONA cornering. ?basis=realized (default) | projected — both
// measured against the SAME fixed realized bar (see cornering_metrics.py
// docstring for the attribution reasoning and the staleness caveat that
// must also ship in any UI tooltip). Response: { league: [per-position HHI,
// bar, elite totals, top holder], rosters: [per (position, roster) VONA,
// share, elite count] }. NOTE: compare HHIs within a basis, not across —
// projection shrinkage pulls marginal players under the bar, which
// mechanically concentrates the projected pool.
r.get('/leagues/:leagueId/cornering', async (req, res) => {
  const lid = req.params.leagueId;
  const basis = req.query.basis === 'projected' ? 'projected' : 'realized';
  const league = await query(
    `SELECT position, replacement_bar, bar_currency, hhi, elite_total,
            top_roster_id, top_share, n_unprojected
     FROM positional_cornering_league
     WHERE league_id = ? AND basis = ?
       AND as_of_date = (SELECT MAX(as_of_date) FROM positional_cornering_league
                         WHERE league_id = ? AND basis = ?)
     ORDER BY position`,
    [lid, basis, lid, basis]
  );
  const rosters = await query(
    `SELECT position, roster_id, vona, vona_share, elite_count
     FROM positional_cornering
     WHERE league_id = ? AND basis = ?
       AND as_of_date = (SELECT MAX(as_of_date) FROM positional_cornering
                         WHERE league_id = ? AND basis = ?)
     ORDER BY position, vona_share DESC`,
    [lid, basis, lid, basis]
  );
  res.json({ basis, league, rosters });
});

export default r;



PS C:\Users\delro\OneDrive\Documents\myanalysis\dynasty-portfolio\etl> python project_production.py --db data/dynasty.db
training m1_ridge_v1 on 2019-2025 (lambda validated on 2025) ...
  fitted positions: ['QB', 'RB', 'TE', 'WR'] on 13,821 training pairs
projected rows: 1340 rostered assets | NULL projections (no DP rank at as-of, mostly 2026 rookies): 271

verification — projected Production Share sums per league:
  Paid In Full: shares sum 1.000000
  The Drew League: shares sum 1.000000
  Ballers University: shares sum 1.000000
  The Land of Punt: shares sum 1.000000
PS C:\Users\delro\OneDrive\Documents\myanalysis\dynasty-portfolio\etl> python cornering_metrics.py --db data/dynasty.db
Paid In Full: 14 rosters solved (7 lineup slots; skipped: ['DEF', 'K']); greedy diverged on 0 rosters
The Land of Punt: 14 rosters solved (9 lineup slots; skipped: ['DB', 'DL', 'IDP_FLEX', 'LB']); greedy diverged on 0 rosters
Ballers University: 14 rosters solved (9 lineup slots; skipped: ['DB', 'DL', 'IDP_FLEX', 'LB']); greedy diverged on 0 rosters
The Drew League: 14 rosters solved (10 lineup slots; skipped: none); greedy diverged on 0 rosters
total Hungarian gain over greedy across all rosters: 0.00 pts/wk
PS C:\Users\delro\OneDrive\Documents\myanalysis\dynasty-portfolio\etl> python lineup_solver.py --db data/dynasty.db --source v_player_value_projected
source 'v_player_value_projected' does not exist in data/dynasty.db.
Build order:
  1. python project_production.py --db data/dynasty.db
  2. python cornering_metrics.py  --db data/dynasty.db
  3. re-run this command
