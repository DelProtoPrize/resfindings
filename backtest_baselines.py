"""
backtest_baselines.py — build-order #3: first predictions, first skill scores.

Logs two baselines into the append-only harness tables and evaluates them:
  B0 'b0_lastseason'  yhat_total = prev-season ppg * weeks_remaining.
                      The embarrassingly-simple bar. No rank input.
  B1 'b1_ecr_v1'      FLAT positional-rank -> points curve (Correction 1:
                      deliberately NOT settings-segmented, because FantasyPros
                      ECR is not settings-aware). Rank = ECR-2QB order within
                      position at as_of, from the dp_values_history archive.
                      Curve trained on EXPANDING WINDOW of past seasons only.

Targets (both in CANONICAL Drew League points, REG-only):
  total_pts  — sum over remaining weeks; absence scores 0 (availability
               baked in: this is what dynasty value actually prices)
  ppg_active — mean over weeks actually played (skill-when-on-field)

Protocol:
  - as_of grid: the day before the first game of weeks {1,5,9,13}, seasons
    2020-2025 for testing (2019 exists only to train 2020's curve).
  - ALL model inputs flow through build_features(as_of,...) — the audited
    chokepoint. Realized outcomes are computed here (evaluation side may see
    the future; predictions may not).
  - predictions are append-only rows written once; evaluation joins them to
    realized outcomes later. model_runs records the train window + grid.
  - Headline: skill = 1 - MAE_B1/MAE_B0 on COMMON SUPPORT (players with both
    a rank and a prior season), per position and pooled, with a player-block
    bootstrap CI (weekly errors within a player are correlated; resampling
    rows would fake precision). B1's extra coverage (rookies have ranks but
    no prior season) is reported separately, not mixed into the comparison.

Usage:  python backtest_baselines.py --db data/dynasty.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

import numpy as np
import pandas as pd

from build_features import build_features, visible_weeks

GRID_WEEKS = (1, 5, 9, 13)
TEST_SEASONS = range(2020, 2026)
CURVE_SMOOTH_WINDOW = 5

DDL = """
CREATE TABLE IF NOT EXISTS model_runs (
    model_id TEXT, run_ts TEXT DEFAULT (datetime('now')),
    train_window TEXT, grid TEXT,
    PRIMARY KEY (model_id, train_window)
);
CREATE TABLE IF NOT EXISTS predictions (
    model_id TEXT, as_of TEXT, sleeper_id TEXT, horizon TEXT, target TEXT,
    yhat REAL,
    PRIMARY KEY (model_id, as_of, sleeper_id, horizon, target)
);
CREATE TABLE IF NOT EXISTS evaluations (
    model_id TEXT, test_season INTEGER, horizon TEXT, position TEXT,
    metric TEXT, value REAL, n INTEGER,
    PRIMARY KEY (model_id, test_season, horizon, position, metric)
);
"""


# ---------------------------------------------------------------------------
# protocol pieces
# ---------------------------------------------------------------------------

def as_of_grid(con: sqlite3.Connection, season: int) -> list[str]:
    """Day before the first game of each grid week."""
    out = []
    for wk in GRID_WEEKS:
        row = con.execute(
            "SELECT first_game_date FROM nfl_week_calendar "
            "WHERE season=? AND week=?", (season, wk)).fetchone()
        if row:
            out.append(str((pd.Timestamp(row[0]) - pd.Timedelta(days=1)).date()))
    return out


def realized(con: sqlite3.Connection, as_of: str, season: int,
             league_id: str) -> pd.DataFrame:
    """Evaluation-side targets over the season's remaining (non-visible)
    weeks: total_pts (absence = 0) and ppg_active."""
    vis = visible_weeks(con, as_of)
    vis_wks = set(vis[vis.season == season].week)
    cal = pd.read_sql_query(
        "SELECT week FROM nfl_week_calendar WHERE season=?", con,
        params=(season,))
    remaining = sorted(set(cal.week) - vis_wks)
    if not remaining:
        return pd.DataFrame(columns=["sleeper_id", "real_total", "real_ppg"])
    q = ("SELECT sleeper_id, SUM(pts) AS real_total, AVG(pts) AS real_ppg "
         "FROM outcomes WHERE league_id=? AND season=? AND week IN (%s) "
         "GROUP BY sleeper_id" % ",".join("?" * len(remaining)))
    return pd.read_sql_query(q, con, params=(league_id, season, *remaining))


VALID_POSITIONS = ("QB", "RB", "WR", "TE")


def features_with_rank(con, as_of: str, season: int) -> pd.DataFrame:
    """Chokepoint features + positional ECR rank (B1's only model input).
    Restricted to fantasy positions — the crosswalk carries a few junk
    position labels (e.g. 'XX') that would otherwise leak into evaluation."""
    f = build_features(con, as_of=as_of, season=season, horizon="ros")
    f = f[f.position.isin(VALID_POSITIONS)]
    f["pos_rank"] = f.groupby("position")["dp_ecr_2qb"].rank(method="first")
    return f


# ---------------------------------------------------------------------------
# B1 curve
# ---------------------------------------------------------------------------

def train_b1_curve(con, train_seasons: list[int], league_id: str,
                   cache: dict) -> pd.DataFrame:
    """(position, pos_rank) -> expected per-remaining-week total rate and
    expected ppg_active, pooled over the grid as_ofs of train seasons and
    rank-smoothed. Per-week rate (not raw total) so different as_ofs with
    different weeks_remaining pool coherently."""
    pairs = []
    for s in train_seasons:
        for ao in as_of_grid(con, s):
            f = cache.setdefault((s, ao), features_with_rank(con, ao, s))
            r = realized(con, ao, s, league_id)
            m = f[f.pos_rank.notna()][
                ["sleeper_id", "position", "pos_rank", "weeks_remaining"]
            ].merge(r, on="sleeper_id", how="left")
            m["real_total"] = m.real_total.fillna(0.0)
            m["rate"] = m.real_total / m.weeks_remaining.clip(lower=1)
            pairs.append(m[["position", "pos_rank", "rate", "real_ppg"]])
    p = pd.concat(pairs, ignore_index=True)
    curve = (p.groupby(["position", "pos_rank"], as_index=False)
              .agg(rate=("rate", "mean"), ppg=("real_ppg", "mean"),
                   n=("rate", "size")))
    # smooth along rank within position (rank means are noisy at depth)
    curve = curve.sort_values(["position", "pos_rank"])
    for col in ("rate", "ppg"):
        curve[col] = (curve.groupby("position")[col]
                      .transform(lambda s: s.rolling(CURVE_SMOOTH_WINDOW,
                                                     center=True,
                                                     min_periods=1).mean()))
    return curve


def b1_predict(f: pd.DataFrame, curve: pd.DataFrame) -> pd.DataFrame:
    """Look up each ranked player's curve value; ranks deeper than the curve
    clamp to the deepest trained rank."""
    out = f[f.pos_rank.notna()][["sleeper_id", "position", "pos_rank",
                                 "weeks_remaining"]].copy()
    deep = curve.groupby("position").pos_rank.max().rename("max_rank")
    out = out.merge(deep, on="position", how="left")
    out["lookup_rank"] = np.minimum(out.pos_rank, out.max_rank)
    out = out.merge(curve, left_on=["position", "lookup_rank"],
                    right_on=["position", "pos_rank"],
                    suffixes=("", "_c"), how="left")
    out["yhat_total"] = out.rate * out.weeks_remaining
    out["yhat_ppg"] = out.ppg
    return out[["sleeper_id", "yhat_total", "yhat_ppg"]]


# ---------------------------------------------------------------------------
# run + evaluate
# ---------------------------------------------------------------------------

def log_predictions(con, model_id: str, as_of: str, df: pd.DataFrame) -> None:
    rows = []
    for _, r in df.iterrows():
        if pd.notna(r.yhat_total):
            rows.append((model_id, as_of, r.sleeper_id, "ros", "total_pts",
                         float(r.yhat_total)))
        if pd.notna(r.yhat_ppg):
            rows.append((model_id, as_of, r.sleeper_id, "ros", "ppg_active",
                         float(r.yhat_ppg)))
    con.executemany("INSERT OR REPLACE INTO predictions VALUES (?,?,?,?,?,?)",
                    rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/dynasty.db")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    need = {"outcomes": "outcomes_etl.py", "outcomes_provenance": "outcomes_etl.py",
            "nfl_week_calendar": "outcomes_etl.py",
            "dp_values_history": "dp_archive_etl.py",
            "id_crosswalk": "dp_archive_etl.py"}
    missing = {t: src for t, src in need.items() if t not in have}
    if missing:
        sys.exit("Missing tables -> run these first:\n" + "\n".join(
            f"  {t}  ({src})" for t, src in sorted(missing.items())))
    con.executescript(DDL)
    league_id = con.execute("SELECT league_id FROM outcomes_provenance "
                            "WHERE is_canonical=1").fetchone()[0]
    cache: dict = {}
    eval_rows, pooled = [], []

    for S in TEST_SEASONS:
        train = [s for s in range(2019, S)]
        curve = train_b1_curve(con, train, league_id, cache)
        con.execute("INSERT OR REPLACE INTO model_runs "
                    "(model_id, train_window, grid) VALUES (?,?,?)",
                    ("b1_ecr_v1", f"seasons<{S}", str(GRID_WEEKS)))
        con.execute("INSERT OR REPLACE INTO model_runs "
                    "(model_id, train_window, grid) VALUES (?,?,?)",
                    ("b0_lastseason", f"seasons<{S}", str(GRID_WEEKS)))

        season_frames = []
        for ao in as_of_grid(con, S):
            f = cache.setdefault((S, ao), features_with_rank(con, ao, S))
            b1 = b1_predict(f, curve).assign(model="b1_ecr_v1")
            b0 = f[f.ppg_prev_season.notna()][
                ["sleeper_id", "ppg_prev_season", "weeks_remaining"]].copy()
            b0["yhat_total"] = b0.ppg_prev_season * b0.weeks_remaining
            b0["yhat_ppg"] = b0.ppg_prev_season
            b0 = b0[["sleeper_id", "yhat_total", "yhat_ppg"]].assign(
                model="b0_lastseason")
            log_predictions(con, "b1_ecr_v1", ao, b1)
            log_predictions(con, "b0_lastseason", ao, b0)

            r = realized(con, ao, S, league_id)
            f_meta = f[["sleeper_id", "position"]]
            both = (b1.merge(b0, on="sleeper_id",
                             suffixes=("_b1", "_b0"))   # common support
                      .merge(f_meta, on="sleeper_id")
                      .merge(r, on="sleeper_id", how="left"))
            both["real_total"] = both.real_total.fillna(0.0)
            both["as_of"] = ao
            season_frames.append(both)
            cov_b1 = len(b1)
            cov_b0 = len(b0)
        sf = pd.concat(season_frames, ignore_index=True)
        sf["ae_b1"] = (sf.yhat_total_b1 - sf.real_total).abs()
        sf["ae_b0"] = (sf.yhat_total_b0 - sf.real_total).abs()
        pooled.append(sf)

        for pos, g in sf.groupby("position"):
            mae1, mae0 = g.ae_b1.mean(), g.ae_b0.mean()
            sp1 = g[["yhat_total_b1", "real_total"]].corr(
                method="spearman").iloc[0, 1]
            sp0 = g[["yhat_total_b0", "real_total"]].corr(
                method="spearman").iloc[0, 1]
            skill = 1 - mae1 / mae0
            for mid, mae, sp in (("b1_ecr_v1", mae1, sp1),
                                 ("b0_lastseason", mae0, sp0)):
                eval_rows += [
                    (mid, S, "ros", pos, "mae_total", float(mae), len(g)),
                    (mid, S, "ros", pos, "spearman_total", float(sp), len(g))]
            eval_rows.append(("b1_ecr_v1", S, "ros", pos, "skill_vs_b0",
                              float(skill), len(g)))
        print(f"{S}: common-support n/as_of≈{len(sf)//len(as_of_grid(con,S))}, "
              f"B1-only coverage (rookies etc.) ≈{cov_b1 - len(sf)//len(as_of_grid(con,S))}/as_of")

    con.executemany("INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?,?,?,?)",
                    eval_rows)
    con.commit()

    # ---- headline -------------------------------------------------------------
    allf = pd.concat(pooled, ignore_index=True)
    print("\n=== ros total_pts, common support, 2020-2025 pooled ===")
    tab = (allf.groupby("position")
           .apply(lambda g: pd.Series({
               "n": len(g), "MAE_B0": g.ae_b0.mean(), "MAE_B1": g.ae_b1.mean(),
               "skill_B1_vs_B0": 1 - g.ae_b1.mean() / g.ae_b0.mean(),
               "spearman_B1": g[["yhat_total_b1", "real_total"]]
                   .corr(method="spearman").iloc[0, 1]}),
               include_groups=False))
    print(tab.round(3).to_string())
    mae1, mae0 = allf.ae_b1.mean(), allf.ae_b0.mean()
    print(f"\npooled: MAE_B0={mae0:.2f}  MAE_B1={mae1:.2f}  "
          f"skill={1 - mae1 / mae0:.3f}")

    # player-block bootstrap CI on pooled skill
    rng = np.random.default_rng(0)
    by_player = {pid: g[["ae_b1", "ae_b0"]].to_numpy()
                 for pid, g in allf.groupby("sleeper_id")}
    pids = list(by_player)
    skills = []
    for _ in range(1000):
        take = rng.choice(len(pids), size=len(pids), replace=True)
        arr = np.concatenate([by_player[pids[i]] for i in take])
        skills.append(1 - arr[:, 0].mean() / arr[:, 1].mean())
    lo, hi = np.percentile(skills, [2.5, 97.5])
    print(f"player-block bootstrap 95% CI on pooled skill: "
          f"[{lo:.3f}, {hi:.3f}]")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
