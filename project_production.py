"""
project_production.py — Step 3: projected production share.

WHAT THIS IS: the validated m1 ridge (backtested through the point-in-time
harness; skill logged in `evaluations`, rendered in Model Lab) fitted on ALL
completed seasons, then used to project per-week scoring for currently
rostered players at today's as-of. The VBD math (replacement level, VORP,
0-10000 scaling) is then RE-RUN on the projection, mirroring the realized
layer's formulas.

WHAT THIS IS NOT (the label that ships with every number):
  - At a June (preseason) as-of, m1's validated skill vs the flat-ECR
    baseline is a statistical NULL (pooled +0.7%, CI [-0.8%, +2.1%];
    week-1 analog -1.0%). Its real, CI-clearing edge (+1.7%) is in-season.
    Projected shares at this time of year are therefore "market-quality,
    production-aware" — not claimed to beat the market. The brief says a
    null is a result; this is that result, shipped with its label.
  - Players without a DP rank at the as-of (mostly incoming rookies not yet
    in the crosswalk) get NULL projections — a dash, never a fudge.

REGRESSION EFFECTS (fitted, not hand-tuned — per the brief):
  (a) positive regression for young/injured: age_asof, availability_w17,
      and the trailing-production-vs-rank gap (b1_rate and ppg windows are
      both features; ridge fits their difference);
  (b) age curve: age_asof per position — with the survivorship caveat: only
      good players survive to old age, so naive curves understate decline.
      The fitted coefficient is conditional on rank, which absorbs much of
      the survivorship distortion, but it remains a caveat, not a claim.
  (c) efficiency mean-reversion (TD rate, yds/touch vs role): NOT YET a
      feature — outcomes carries points, not component stats. Queued as m2;
      requires an outcomes_etl extension. Stated here so the gap is owned.

REPLACEMENT RECOVERY (flag for repo cross-check): points_model.py is not in
this environment, so per-league replacement ranks K are recovered from the
realized layer itself: replacement_ppg = ppg - vorp (constant per
league-position), K = rank of that ppg within the league's rostered pool.
K is then applied to the PROJECTED rostered pool (rank-preserved, self-
consistent). Cross-check K against points_model.py's constants in the repo —
recovered Drew K (29/43/44/26, rostered pool) sits near the spec
(31/46/44/29, wider pool); within-pool consistency is what the share math
needs, but the constants should be reconciled at integration.

Table written (idempotent per as_of):
  player_projected_value(as_of_date, league_id, roster_id, player_id,
      player_name, position, b1_rate, ppg_proj, replacement_ppg,
      vorp_proj, vbd_proj, model_id, trained_through)

Run:  python project_production.py --db data/dynasty.db
      (after backtest_baselines.py + projection_model.py have ever run;
       imports both — keep the three scripts in the same directory)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date

import numpy as np
import pandas as pd

from backtest_baselines import (as_of_grid, b1_predict, features_with_rank,
                                train_b1_curve)
from projection_model import (MODEL_ID, PROD_FEATURES, assemble_xy,
                              fit_per_position, make_pairs)

TRAIN_SEASONS = list(range(2019, 2026))   # all completed seasons
VAL_SEASON = 2025                          # lambda selection only; refit on all
PROJECT_SEASON = 2026

DDL = """
CREATE TABLE IF NOT EXISTS player_projected_value (
    as_of_date TEXT, league_id TEXT, roster_id INTEGER,
    player_id TEXT, player_name TEXT, position TEXT,
    b1_rate REAL, ppg_proj REAL, replacement_ppg REAL,
    vorp_proj REAL, vbd_proj INTEGER,
    model_id TEXT, trained_through INTEGER,
    PRIMARY KEY (as_of_date, league_id, player_id)
);
"""


def recover_replacement_ranks(con) -> dict[tuple[str, str], int]:
    """K per (league, position), recovered from the realized layer:
    replacement_ppg = ppg - vorp; K = its rank in the rostered pool."""
    ks: dict[tuple[str, str], int] = {}
    leagues = [r[0] for r in con.execute(
        "SELECT DISTINCT league_id FROM v_player_value")]
    for lid in leagues:
        for pos in ("QB", "RB", "WR", "TE"):
            rows = con.execute(
                "SELECT ppg, vorp FROM v_player_value "
                "WHERE league_id=? AND position=? AND ppg IS NOT NULL "
                "AND snapshot_date=(SELECT MAX(snapshot_date) "
                "  FROM v_player_value WHERE league_id=?) "
                "ORDER BY ppg DESC", (lid, pos, lid)).fetchall()
            repl = next((round(p - v, 4) for p, v in rows if v and v > 0), None)
            if repl is not None:
                ks[(lid, pos)] = sum(1 for p, _ in rows if p > repl) + 1
    return ks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/dynasty.db")
    ap.add_argument("--as-of", default=str(date.today()))
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    con.executescript(DDL)
    canonical = con.execute("SELECT league_id FROM outcomes_provenance "
                            "WHERE is_canonical=1").fetchone()[0]

    # ---- train the validated model on all completed seasons -----------------
    cache: dict = {}
    print(f"training {MODEL_ID} on {TRAIN_SEASONS[0]}-{TRAIN_SEASONS[-1]} "
          f"(lambda validated on {VAL_SEASON}) ...")
    curve = train_b1_curve(con, TRAIN_SEASONS, canonical, cache)
    pairs = make_pairs(con, TRAIN_SEASONS, canonical, curve, cache)
    df, medians = assemble_xy(pairs, None)
    train = df[df.season != VAL_SEASON] if "season" in df.columns else \
        df[~df.as_of.str.startswith(str(VAL_SEASON))]
    val = df.drop(train.index)
    models = fit_per_position(train, val)
    print(f"  fitted positions: {sorted(models)} on {len(df):,} training pairs")

    # ---- project the current world ------------------------------------------
    f = features_with_rank(con, args.as_of, PROJECT_SEASON)
    f = f[f.pos_rank.notna()].copy()          # unranked -> honest NULL (no row)
    b1 = b1_predict(f, curve).rename(columns={"yhat_total": "b1_total"})
    f = f.merge(b1[["sleeper_id", "b1_total"]], on="sleeper_id")
    f["b1_rate"] = f.b1_total / f.weeks_remaining.clip(lower=1)
    f, _ = assemble_xy([f], medians)          # impute with TRAIN medians only
    cols = ["b1_rate"] + PROD_FEATURES + ["has_history"]
    f["ppg_proj"] = np.nan
    for pos, (model, _lam) in models.items():
        m = f.position == pos
        if m.any():
            f.loc[m, "ppg_proj"] = np.clip(
                model.predict(f.loc[m, cols].to_numpy(float)), 0, None)

    # ---- map onto current rosters, re-run VBD math on the projection --------
    ks = recover_replacement_ranks(con)
    # Latest season per league THAT HAS VALUE DATA (same rule as the lineup
    # solver) — historical league-season rows share snapshot dates and must
    # not be swept in by a global-latest filter.
    rostered = pd.read_sql_query(
        "SELECT v.league_id, v.roster_id, v.player_id, v.player_name, v.position "
        "FROM v_player_value v "
        "JOIN dim_leagues l ON l.league_id = v.league_id "
        "WHERE l.season = (SELECT MAX(d2.season) FROM dim_leagues d2 "
        "    JOIN v_player_value vv ON vv.league_id = d2.league_id "
        "    WHERE d2.league_name = l.league_name) "
        "AND v.snapshot_date = (SELECT MAX(snapshot_date) "
        "    FROM v_player_value WHERE league_id = v.league_id)", con)
    proj = rostered.merge(
        f[["sleeper_id", "b1_rate", "ppg_proj"]],
        left_on="player_id", right_on="sleeper_id", how="left")

    con.execute("DELETE FROM player_projected_value WHERE as_of_date=?",
                (args.as_of,))
    n_null = 0
    for lid, g in proj.groupby("league_id"):
        g = g.copy()
        g["replacement_ppg"] = np.nan
        for pos, gp in g.groupby("position"):
            k = ks.get((lid, pos))
            if k is None:
                continue
            ranked = gp.ppg_proj.dropna().sort_values(ascending=False)
            if not len(ranked):
                continue
            repl = ranked.iloc[min(k, len(ranked)) - 1]
            g.loc[gp.index, "replacement_ppg"] = repl
        g["vorp_proj"] = (g.ppg_proj - g.replacement_ppg).clip(lower=0).round(2)
        vmax = g.vorp_proj.max()
        g["vbd_proj"] = (g.vorp_proj / vmax * 10000).round() if vmax and vmax > 0 else np.nan
        n_null += int(g.ppg_proj.isna().sum())
        con.executemany(
            "INSERT INTO player_projected_value VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(args.as_of, lid, int(r.roster_id), r.player_id, r.player_name,
              r.position,
              None if pd.isna(r.b1_rate) else round(float(r.b1_rate), 3),
              None if pd.isna(r.ppg_proj) else round(float(r.ppg_proj), 3),
              None if pd.isna(r.replacement_ppg) else round(float(r.replacement_ppg), 3),
              None if pd.isna(r.vorp_proj) else float(r.vorp_proj),
              None if pd.isna(r.vbd_proj) else int(r.vbd_proj),
              MODEL_ID, TRAIN_SEASONS[-1]) for r in g.itertuples()])
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM player_projected_value "
                        "WHERE as_of_date=?", (args.as_of,)).fetchone()[0]
    print(f"projected rows: {total} rostered assets | NULL projections "
          f"(no DP rank at as-of, mostly 2026 rookies): {n_null}")

    # ---- verification --------------------------------------------------------
    print("\nverification — projected Production Share sums per league:")
    for lid, nm in con.execute(
            "SELECT DISTINCT p.league_id, l.league_name "
            "FROM player_projected_value p JOIN dim_leagues l "
            "ON l.league_id=p.league_id WHERE p.as_of_date=?", (args.as_of,)):
        tot = con.execute(
            "SELECT SUM(vbd_proj) FROM player_projected_value "
            "WHERE league_id=? AND as_of_date=?", (lid, args.as_of)).fetchone()[0]
        sh = con.execute(
            "SELECT SUM(s) FROM (SELECT CAST(SUM(vbd_proj) AS REAL)/? AS s "
            "FROM player_projected_value WHERE league_id=? AND as_of_date=? "
            "GROUP BY roster_id)", (tot, lid, args.as_of)).fetchone()[0]
        print(f"  {nm}: shares sum {sh:.6f}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
