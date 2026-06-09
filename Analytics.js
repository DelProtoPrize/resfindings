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

// Roster drill-down: one team's full asset list with values, age, arbitrage.
r.get('/leagues/:id/rosters/:rosterId', async (req, res, next) => {
  try {
    const sql = `
      SELECT player_name, position, age, nfl_team,
             fp_market_value, fc_market_value, fp_ecr_2qb, fc_trend_30day,
             arb_delta_fp_minus_fc
      FROM v_player_market
      WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM v_player_market)
        AND league_id = ? AND roster_id = ?
      ORDER BY (fp_market_value IS NULL), fp_market_value DESC
    `;
    res.json(await query(sql, [req.params.id, Number(req.params.rosterId)]));
  } catch (e) { next(e); }
});

export default r;
