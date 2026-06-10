"""
build_features.py (v2) — the single point-in-time chokepoint for the harness.

EVERY baseline and model assembles inputs through build_features(); leakage can
occur in exactly one auditable place. Hard rules (enforced):
  1. Every source read filters knowledge_date <= as_of IN SQL, not pandas.
  2. nflverse weekly stats admitted per (season, week) via the week calendar:
     a week is visible only if its LAST game date <= as_of.
  3. No current-day FP/FC values for a past as_of. DP values come from
     dp_values_history (git-archaeology table, sleeper_id resolved at load);
     FC from fc_values_snapshots (prospective accrual only — features are NaN
     before the first snapshot, never backfilled).
  4. Deterministic given (as_of, season, horizon). Output never holds a target.

The self-audit enforces TWO DISTINCT invariants (review reconciliation):
  - LEAKAGE re-check (date-based): no visible week may END after as_of, and no
    joined snapshot may postdate as_of. Redundant with the SQL filters by
    design — a guard that re-derives the property it protects.
  - VALIDITY check (cross-season): no visible week may belong to a season
    AFTER the requested `season`. Such weeks are not a leak (they're <= as_of),
    but they mean as_of postdates the start of season+1, so the outcome window
    for every horizon is already partially realized — logging a "prediction"
    there grades hindsight as foresight. Refused, loudly.

Expected tables:
  outcomes(league_id, sleeper_id, season, week, pts, active)          [LIVE: outcomes_etl,
                    2019-2025 REG, scored per league config]
  outcomes_provenance(league_id, is_canonical, ...)                   [LIVE: Drew=canonical]
  nfl_week_calendar(season, week, first_game_date, last_game_date)    [LIVE: outcomes_etl]
  dp_values_history(knowledge_date, player_key, sleeper_id, ecr_1qb, ecr_2qb,
                    value_1qb, value_2qb, draft_year, ...)            [LIVE: 318
                    snapshots 2019-04-06..2026-06-05 via dp_archive_etl]
  fc_values_snapshots(knowledge_date, sleeper_id, fc_value, fc_trend_30day,
                      num_qbs, num_teams, ppr)                        [pending ETL;
                      NOTE: TEP is NOT here — it joins from dim_leagues]
  id_crosswalk(sleeper_id, gsis_id, fp_id, merge_name, position, birthdate)
                    [LIVE: materialized by dp_archive_etl from db_playerids;
                    dim_players has no birthdate, only integer age]
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

# Bumped whenever the expected warehouse schema changes. Consumers
# (backtest_baselines etc.) check this at import so a stale copy of THIS file
# fails with instructions instead of a mid-run "no such table" traceback.
SCHEMA_VERSION = 2   # v2: production features read from `outcomes` (canonical
                     # league points), not the retired nflverse_weekly table

HORIZONS = ("next_week", "ros", "next_season")
WINDOWS = (4, 8, 17)


# --------------------------------------------------------------------------- #
# Visibility primitives — the functions that define "what is knowable"
# --------------------------------------------------------------------------- #

def visible_weeks(con: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """(season, week, last_game_date) for weeks fully completed by as_of.

    Carries last_game_date so the audit can RE-CHECK the visibility property
    instead of trusting this function. Filter on last_game_date: a week with
    any game after as_of is invisible in its entirety (kills the
    Thursday-stats-in-a-Sunday-as_of leak that row filtering would allow).
    """
    return pd.read_sql_query(
        "SELECT season, week, last_game_date FROM nfl_week_calendar "
        "WHERE last_game_date <= ? ORDER BY season, week",
        con, params=(as_of,),
    )


def latest_dp_snapshot(con: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """Most recent DP snapshot at or before as_of. Joins on sleeper_id, which
    dp_archive_etl resolved at load (fp_id era: fp_id join, 99.6%; pre-2020
    dyno era: merge_name join, 96.7%). Unmatched rows carry NULL sleeper_id
    and drop out of the merge — that loss rate is reported by the loader, not
    hidden here."""
    kd = con.execute(
        "SELECT MAX(knowledge_date) FROM dp_values_history "
        "WHERE knowledge_date <= ?", (as_of,),
    ).fetchone()[0]
    if kd is None:  # as_of predates 2019-04-06
        return pd.DataFrame(columns=["sleeper_id", "dp_ecr_1qb", "dp_ecr_2qb",
                                     "dp_value_2qb", "draft_year",
                                     "dp_snapshot_date"])
    return pd.read_sql_query(
        "SELECT sleeper_id, ecr_1qb AS dp_ecr_1qb, ecr_2qb AS dp_ecr_2qb, "
        "       value_2qb AS dp_value_2qb, draft_year, "
        "       knowledge_date AS dp_snapshot_date "
        "FROM dp_values_history "
        "WHERE knowledge_date = ? AND sleeper_id IS NOT NULL "
        "ORDER BY sleeper_id",
        con, params=(kd,),
    ).drop_duplicates("sleeper_id")


def latest_fc_snapshot(con: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """Most recent FC snapshot <= as_of. Prospective-only: before the first
    accrued snapshot (2026-06) this is empty and downstream features are NaN —
    the honest representation, not a backfill."""
    kd = con.execute(
        "SELECT MAX(knowledge_date) FROM fc_values_snapshots "
        "WHERE knowledge_date <= ?", (as_of,),
    ).fetchone()[0]
    if kd is None:
        return pd.DataFrame(columns=["sleeper_id", "fc_value", "fc_trend_30day",
                                     "num_qbs", "num_teams", "ppr",
                                     "fc_snapshot_date"])
    return pd.read_sql_query(
        "SELECT sleeper_id, fc_value, fc_trend_30day, num_qbs, num_teams, ppr, "
        "       knowledge_date AS fc_snapshot_date "
        "FROM fc_values_snapshots WHERE knowledge_date = ? ORDER BY sleeper_id",
        con, params=(kd,),
    ).drop_duplicates("sleeper_id")


# --------------------------------------------------------------------------- #
# The chokepoint
# --------------------------------------------------------------------------- #

def build_features(con: sqlite3.Connection, as_of: str, season: int,
                   horizon: str, league_id: str | None = None) -> pd.DataFrame:
    """One row per sleeper_id; features only; facts dated <= as_of only.

    Production features are expressed in LEAGUE POINTS — by default the
    canonical league (The Drew League, per outcomes_provenance), so features
    and targets share a currency. Pass league_id to build features under a
    different config (e.g. a best-ball league's scoring)."""
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {HORIZONS}")
    if league_id is None:
        row = con.execute("SELECT league_id FROM outcomes_provenance "
                          "WHERE is_canonical=1").fetchone()
        if row is None:
            raise RuntimeError("No canonical league in outcomes_provenance — "
                               "run outcomes_etl.py first.")
        league_id = row[0]

    weeks = visible_weeks(con, as_of)
    season_weeks = weeks[weeks.season == season]

    # --- production block: visibility enforced AT READ via temp-table join ---
    con.execute("DROP TABLE IF EXISTS _vis_weeks")
    con.execute("CREATE TEMP TABLE _vis_weeks (season INT, week INT, "
                "PRIMARY KEY (season, week))")
    con.executemany(
        "INSERT INTO _vis_weeks VALUES (?, ?)",
        list(weeks[["season", "week"]].itertuples(index=False, name=None)))
    prod = pd.read_sql_query(
        """
        SELECT o.sleeper_id, o.season, o.week, o.pts
        FROM outcomes o
        JOIN _vis_weeks v ON v.season = o.season AND v.week = o.week
        WHERE o.league_id = ?
        ORDER BY o.sleeper_id, o.season, o.week
        """,
        con, params=(league_id,),
    )

    xwalk = pd.read_sql_query(
        "SELECT sleeper_id, position, birthdate, draft_year AS xw_draft_year "
        "FROM id_crosswalk ORDER BY sleeper_id", con) \
        if _has_col(con, "id_crosswalk", "draft_year") else \
        pd.read_sql_query(
            "SELECT sleeper_id, position, birthdate FROM id_crosswalk "
            "ORDER BY sleeper_id", con).assign(xw_draft_year=np.nan)

    rows = []
    asof_ts = pd.Timestamp(as_of)
    for pid, g in prod.groupby("sleeper_id", sort=True):
        g = g.sort_values(["season", "week"])
        pts = g["pts"].to_numpy()
        feat = {"sleeper_id": pid}
        for w in WINDOWS:
            tail = pts[-w:]
            feat[f"ppg_w{w}"] = float(np.mean(tail)) if tail.size else np.nan
            feat[f"tot_pts_w{w}"] = float(np.sum(tail)) if tail.size else np.nan
        t8 = pts[-8:]
        feat["sd_pts_w8"] = float(np.std(t8, ddof=1)) if t8.size > 2 else np.nan
        m8 = np.mean(t8) if t8.size else np.nan
        feat["cv_pts_w8"] = (feat["sd_pts_w8"] / m8
                             if t8.size > 2 and m8 and m8 > 0 else np.nan)
        feat["boom_rate_w8"] = float(np.mean(t8 >= 20)) if t8.size else np.nan
        feat["bust_rate_w8"] = float(np.mean(t8 < 5)) if t8.size else np.nan
        recent = g.tail(17)
        feat["gp_visible_season"] = int((g.season == season).sum())
        feat["availability_w17"] = float(len(recent)) / 17.0
        prev = g[g.season == season - 1]
        feat["ppg_prev_season"] = float(prev.pts.mean()) if len(prev) else np.nan
        rows.append(feat)
    out = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["sleeper_id"])
    out = xwalk.merge(out, on="sleeper_id", how="left")

    # --- market blocks (dated snapshots only) ---------------------------------
    out = out.merge(latest_dp_snapshot(con, as_of), on="sleeper_id", how="left")
    out = out.merge(latest_fc_snapshot(con, as_of), on="sleeper_id", how="left")
    for col, src in (("dp_staleness_days", "dp_snapshot_date"),
                     ("fc_staleness_days", "fc_snapshot_date")):
        out[col] = (asof_ts - pd.to_datetime(out[src], errors="coerce")).dt.days

    # --- identity / clock features --------------------------------------------
    bd = pd.to_datetime(out["birthdate"], errors="coerce")
    out["age_asof"] = (asof_ts - bd).dt.days / 365.25
    dy = out["draft_year"].fillna(out["xw_draft_year"]) \
        if "draft_year" in out.columns else out["xw_draft_year"]
    out["seasons_in_league"] = season - pd.to_numeric(dy, errors="coerce")
    last_visible = int(season_weeks.week.max()) if len(season_weeks) else 0
    total_weeks = con.execute(
        "SELECT MAX(week) FROM nfl_week_calendar WHERE season = ?", (season,)
    ).fetchone()[0] or 18
    out["weeks_remaining"] = max(total_weeks - last_visible, 0)

    # --- self-audit: refuse to return an invalid frame -------------------------
    _audit_point_in_time(out, weeks, as_of, season)

    drop = ["birthdate", "xw_draft_year", "draft_year",
            "dp_snapshot_date", "fc_snapshot_date"]
    out = out.drop(columns=[c for c in drop if c in out.columns])
    return out.sort_values("sleeper_id").reset_index(drop=True)


def _has_col(con: sqlite3.Connection, table: str, col: str) -> bool:
    return col in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def _audit_point_in_time(out: pd.DataFrame, weeks: pd.DataFrame,
                         as_of: str, season: int) -> None:
    """Two invariants, two failure modes — see module docstring."""
    asof_ts = pd.Timestamp(as_of)

    # (1) LEAKAGE re-check, date-based: re-derive what the SQL filters promise.
    if len(weeks):
        mx = pd.to_datetime(weeks["last_game_date"]).max()
        assert pd.isna(mx) or mx <= asof_ts, \
            f"LEAK: a visible week ends {mx} > as_of {as_of}"
    for col in ("dp_snapshot_date", "fc_snapshot_date"):
        if col in out.columns:
            mx = pd.to_datetime(out[col], errors="coerce").max()
            assert pd.isna(mx) or mx <= asof_ts, \
                f"LEAK: {col} {mx} > as_of {as_of}"

    # (2) VALIDITY check, cross-season: visible weeks from season+1 are not a
    # leak (they're <= as_of) but make the (as_of, season) pair degenerate —
    # the outcome window for any horizon has already begun.
    if len(weeks):
        future = weeks[weeks.season > season]
        assert future.empty, (
            f"INVALID BACKTEST CALL: as_of={as_of} makes "
            f"{int(future.season.max())} weeks visible while predicting for "
            f"season={season}; the outcome window has already begun.")

    banned = {"pts", "target", "total_pts", "ppg_active"}
    hit = banned.intersection(out.columns)
    assert not hit, f"LEAK: target-like columns in features: {hit}"


# --------------------------------------------------------------------------- #
# Baseline constructions — Correction 1 enforced by STRUCTURE, not commentary:
# the two stubs have incompatible signatures, so B2 cannot be copy-pasted
# from B1 without deleting the segmentation arguments on purpose.
# --------------------------------------------------------------------------- #

def b1_ecr_rank_to_points_curve(con: sqlite3.Connection, as_of: str,
                                train_seasons: list[int]) -> pd.DataFrame:
    """B1: positional ECR rank -> realized points. FLAT — deliberately NOT
    settings-segmented, because FantasyPros ECR is not settings-aware by
    construction. Learned only from train_seasons fully visible <= as_of.
    Returns (position, rank_bucket, exp_pts).
    Data note: dp_values_history reaches 2019-04-06; values are absent in the
    dyno era but ECR (the input B1 actually needs) is present throughout.
    """
    raise NotImplementedError("Build after outcomes ETL lands (build order #2).")


def b2_fc_value_to_points_curve(con: sqlite3.Connection, as_of: str,
                                train_window_start: str,
                                segment_cols: tuple[str, ...] = (
                                    "num_qbs", "num_teams", "ppr",
                                    "te_premium_value"),
                                ) -> pd.DataFrame:
    """B2: FantasyCalc value -> realized points, SEGMENTED by league settings.
    Returns (segment..., position, value_bucket, exp_pts).

    Structural notes that ARE the correction:
      - segment_cols is part of the signature; a flat B2 is unconstructible
        without consciously removing it.
      - TEP is NOT in fc_values_snapshots — it must be joined from
        dim_leagues.te_premium_value (your leagues: 0 / 0 / 0.25 / 0.5).
      - PROSPECTIVE-ONLY: trains on snapshots >= first accrual (2026-06);
        train_window_start makes that explicit at every call site. There is
        no historical FC series, so B2 has no backtest before that date.
    Evaluation then measures the B2-over-B1 gap at TE/QB — that measured gap
    is the settings-contamination finding. Building B2 flat erases it.
    """
    raise NotImplementedError("Build after FC snapshot accrual has depth.")


if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    db = sys.argv[1] if len(sys.argv) > 1 else next(
        (str(p) for p in ("data/dynasty.db", "etl/data/dynasty.db",
                          "../etl/data/dynasty.db") if _P(p).exists()), None)
    if db is None:
        sys.exit("No dynasty.db found. Usage: python build_features.py "
                 "[path/to/dynasty.db]")
    con = sqlite3.connect(db)
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = {"outcomes", "outcomes_provenance", "nfl_week_calendar",
               "fc_values_snapshots"} - have
    if missing:
        sys.exit(f"DB at {db} is missing harness tables {sorted(missing)} — "
                 f"run outcomes_etl.py (use --seed-fc for fc_values_snapshots); "
                 f"dp_values_history/id_crosswalk come from dp_archive_etl.py.")
    f = build_features(con, as_of="2025-11-04", season=2025, horizon="ros")
    print(f.shape)
    print(f.head(10).to_string())
