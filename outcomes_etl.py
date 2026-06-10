"""
outcomes_etl.py — build-order #2: realized weekly outcomes, scored per league.

Produces the harness tables build_features.py is waiting on:
  outcomes(league_id, sleeper_id, season, week, pts, active)
      One row per player-week PER LEAGUE CONFIG. Different leagues are
      different measurement units of the same underlying game (TEP 0/0/.25/.5,
      PPR .5/1, bonuses) — so "points" is only defined relative to a league.
  nfl_week_calendar(season, week, first_game_date, last_game_date)
      REG season only, from nflverse schedules. Defines week visibility.
  outcomes_provenance(league_id, ...)
      The exact config used, its source season, and the nonzero scoring keys
      that could NOT be mapped to nflverse weekly columns — reported, not
      silently dropped.

Design decisions (the writeup lines):
  - CANONICAL CONFIG PER LEAGUE FAMILY = the latest season's settings, applied
    to all historical seasons. This is a deliberate measurement-lens choice
    ("what would 2019 weeks have scored under today's rules"), documented
    because Land of Punt and Ballers drifted their scoring mid-history.
  - THE DREW LEAGUE IS THE CANONICAL TARGET (is_canonical=1 in provenance):
    start-10 SF TEP-0.5 is the representative dynasty format. B1/B2 projection
    curves train on Drew League points. Land of Punt and Ballers University
    are BEST BALL: that changes how ROSTER points aggregate (auto-optimal
    lineups pay ceiling/boom players), not how a PLAYER's week is scored — so
    best-ball-ness lives in the lineup/win layer, not in this table.
  - SCOPE: QB/RB/WR/TE only. FP/FC values don't cover IDP or K/DEF, so the
    harness can't valuation-test them; IDP keys land in the unmapped report.
  - active=1 means the player has a stat row that week. DNP weeks have NO row;
    summing over a horizon therefore treats absence as 0 — which is exactly
    the availability-baked-in semantics of the total_pts target.
  - TEP enters here for the first time as real scoring: bonus_rec_te pays
    per reception to TEs, bonus_fd_te per TE first down. Never a multiplier.

Usage:
    python outcomes_etl.py --db data/dynasty.db --seasons 2019 2025 [--seed-fc]
    --seed-fc derives fc_values_snapshots from fact_roster_historical_value
    (your accruing FC snapshots) so build_features can run end-to-end today.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

STATS_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
             "stats_player/stats_player_week_{season}.csv")
SCHED_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
             "schedules/games.csv")
POSITIONS = ("QB", "RB", "WR", "TE")
CANONICAL_LEAGUE_NAME = "The Drew League"

DDL = """
CREATE TABLE IF NOT EXISTS outcomes (
    league_id  TEXT NOT NULL,
    sleeper_id TEXT NOT NULL,
    season     INTEGER NOT NULL,
    week       INTEGER NOT NULL,
    pts        REAL NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (league_id, sleeper_id, season, week)
);
CREATE INDEX IF NOT EXISTS ix_outcomes_lsw ON outcomes (league_id, season, week);

CREATE TABLE IF NOT EXISTS nfl_week_calendar (
    season INTEGER NOT NULL,
    week   INTEGER NOT NULL,
    first_game_date TEXT NOT NULL,
    last_game_date  TEXT NOT NULL,
    PRIMARY KEY (season, week)
);

CREATE TABLE IF NOT EXISTS outcomes_provenance (
    league_id     TEXT PRIMARY KEY,
    league_name   TEXT,
    is_canonical  INTEGER,
    is_best_ball  INTEGER,
    source_season INTEGER,
    config_json   TEXT,
    unmapped_nonzero_keys_json TEXT,
    loaded_at     TEXT
);

CREATE TABLE IF NOT EXISTS fc_values_snapshots (
    knowledge_date TEXT NOT NULL,
    sleeper_id     TEXT NOT NULL,
    fc_value       REAL,
    fc_trend_30day REAL,
    num_qbs        INTEGER,
    num_teams      INTEGER,
    ppr            REAL,
    PRIMARY KEY (knowledge_date, sleeper_id)
);
"""

# Sleeper scoring key -> callable(df) returning the stat count it multiplies.
# df carries nflverse weekly columns plus `is_te`.
def _fum(df):     return (df.rushing_fumbles + df.receiving_fumbles
                          + df.sack_fumbles)
def _fum_lost(df): return (df.rushing_fumbles_lost + df.receiving_fumbles_lost
                           + df.sack_fumbles_lost)

STAT_MAP = {
    "pass_yd":   lambda d: d.passing_yards,
    "pass_td":   lambda d: d.passing_tds,
    "pass_int":  lambda d: d.passing_interceptions,
    "pass_2pt":  lambda d: d.passing_2pt_conversions,
    "pass_fd":   lambda d: d.passing_first_downs,
    "rush_yd":   lambda d: d.rushing_yards,
    "rush_td":   lambda d: d.rushing_tds,
    "rush_2pt":  lambda d: d.rushing_2pt_conversions,
    "rush_fd":   lambda d: d.rushing_first_downs,
    "rec":       lambda d: d.receptions,
    "rec_yd":    lambda d: d.receiving_yards,
    "rec_td":    lambda d: d.receiving_tds,
    "rec_2pt":   lambda d: d.receiving_2pt_conversions,
    "rec_fd":    lambda d: d.receiving_first_downs,
    "fum":       _fum,
    "fum_lost":  _fum_lost,
    "st_td":     lambda d: d.special_teams_tds,
    # TE premium: per-reception / per-first-down bonuses gated on position.
    "bonus_rec_te": lambda d: d.receptions * d.is_te,
    "bonus_fd_te":  lambda d: d.receiving_first_downs * d.is_te,
    # threshold bonuses (1 if the game crossed the line)
    "bonus_pass_yd_300": lambda d: (d.passing_yards >= 300).astype(float),
    "bonus_pass_yd_400": lambda d: (d.passing_yards >= 400).astype(float),
    "bonus_rush_yd_100": lambda d: (d.rushing_yards >= 100).astype(float),
    "bonus_rush_yd_200": lambda d: (d.rushing_yards >= 200).astype(float),
    "bonus_rec_yd_100":  lambda d: (d.receiving_yards >= 100).astype(float),
    "bonus_rec_yd_200":  lambda d: (d.receiving_yards >= 200).astype(float),
}

# Nonzero keys we deliberately do not map for QB/RB/WR/TE outcomes. Anything
# nonzero in a config and in neither STAT_MAP nor this set raises.
KNOWN_UNMAPPED = {
    # defense/IDP/K/DEF scoring — out of scope positions
    "int", "sack", "safe", "ff", "blk_kick", "fum_rec", "fum_rec_td",
    "def_td", "xpm", "xpmiss", "fgmiss",
    # play-length bonuses absent from stats_player_week (need PBP)
    "rec_40p", "rush_40p", "pass_cmp_40p",
}
KNOWN_UNMAPPED_PREFIXES = ("idp_", "def_st_", "st_f", "pts_allow", "fgm_",
                           "yds_allow", "bonus_sack", "bonus_tkl",
                           "pass_cmp_4", "pass_int_td")


def canonical_configs(con: sqlite3.Connection) -> pd.DataFrame:
    """Latest-season config per league family, Drew flagged canonical.
    Best-ball flags are set per your league facts: Land of Punt and Ballers
    University are best ball; Drew and Paid In Full are start-your-lineup."""
    leagues = pd.read_sql_query(
        "SELECT league_id, league_name, season, scoring_settings_json "
        "FROM dim_leagues ORDER BY league_name, season", con)
    latest = leagues.groupby("league_name", as_index=False).last()
    latest["is_canonical"] = (latest.league_name == CANONICAL_LEAGUE_NAME).astype(int)
    latest["is_best_ball"] = latest.league_name.isin(
        ["The Land of Punt", "Ballers University"]).astype(int)
    return latest


def score_config(weekly: pd.DataFrame, config: dict) -> tuple[pd.Series, list]:
    """Vectorized scoring of all player-weeks under one config.
    Returns (points, unmapped_nonzero_keys)."""
    pts = pd.Series(0.0, index=weekly.index)
    unmapped = []
    for key, val in config.items():
        if not val:
            continue
        fn = STAT_MAP.get(key)
        if fn is not None:
            pts = pts + float(val) * fn(weekly).fillna(0).astype(float)
        elif key in KNOWN_UNMAPPED or key.startswith(KNOWN_UNMAPPED_PREFIXES):
            unmapped.append(key)
        else:
            raise RuntimeError(
                f"Scoring key '{key}'={val} is nonzero, not in STAT_MAP, and "
                f"not registered as known-unmapped. Map it or register it — "
                f"don't let it silently score as zero.")
    return pts, unmapped


def load_weekly(seasons: list[int]) -> pd.DataFrame:
    frames = []
    for s in seasons:
        df = pd.read_csv(STATS_URL.format(season=s), low_memory=False)
        df = df[(df.season_type == "REG") & (df.position.isin(POSITIONS))]
        frames.append(df)
        print(f"  {s}: {len(df)} REG offense player-weeks")
    out = pd.concat(frames, ignore_index=True).copy()
    out["is_te"] = (out.position == "TE").astype(float)
    return out


def build_calendar(con: sqlite3.Connection, seasons: list[int]) -> None:
    g = pd.read_csv(SCHED_URL, usecols=["season", "week", "game_type", "gameday"])
    g = g[(g.game_type == "REG") & (g.season.isin(seasons))]
    cal = (g.groupby(["season", "week"])
            .gameday.agg(first_game_date="min", last_game_date="max")
            .reset_index())
    con.execute("DELETE FROM nfl_week_calendar WHERE season IN (%s)"
                % ",".join("?" * len(seasons)), seasons)
    cal.to_sql("nfl_week_calendar", con, if_exists="append", index=False)
    print(f"calendar: {len(cal)} season-weeks "
          f"({cal.season.min()}–{cal.season.max()})")


def seed_fc_from_warehouse(con: sqlite3.Connection) -> None:
    """Bootstrap fc_values_snapshots from fact_roster_historical_value.
    FC values were pulled settings-aware for SF/14tm/PPR (Drew settings) —
    recorded as such. One row per (snapshot_date, player), de-duped across
    the leagues a player appears in."""
    n = con.execute("""
        INSERT OR REPLACE INTO fc_values_snapshots
        SELECT snapshot_date, player_id,
               AVG(fc_value_2qb), AVG(fc_trend_30day), 2, 14, 1.0
        FROM fact_roster_historical_value
        WHERE fc_value_2qb IS NOT NULL
        GROUP BY snapshot_date, player_id
    """).rowcount
    con.commit()
    print(f"fc_values_snapshots: seeded {n} rows from warehouse snapshots")


def validate_against_points_model(con: sqlite3.Connection) -> None:
    """Cross-check: our 2025 Drew-config PPG vs points_model's
    player_production_value.ppg for the Drew family. Disagreement here means
    the two scorers diverge — surface it, don't average it away."""
    canon_id = con.execute(
        "SELECT league_id FROM outcomes_provenance WHERE is_canonical=1"
    ).fetchone()[0]
    drew_ids = [r[0] for r in con.execute(
        "SELECT league_id FROM dim_leagues WHERE league_name = ?",
        (CANONICAL_LEAGUE_NAME,))]
    ppv = pd.read_sql_query(
        "SELECT player_id AS sleeper_id, ppg AS ppv_ppg, games FROM "
        "player_production_value WHERE season=2025 AND league_id IN (%s)"
        % ",".join("?" * len(drew_ids)), con, params=drew_ids
    ).drop_duplicates("sleeper_id")  # ppv has one row per league-season id
    if ppv.empty:
        print("validation: no Drew rows in player_production_value — skipped")
        return
    mine = pd.read_sql_query(
        "SELECT sleeper_id, AVG(pts) AS etl_ppg, COUNT(*) AS games_etl "
        "FROM outcomes WHERE league_id=? AND season=2025 GROUP BY sleeper_id",
        con, params=(canon_id,))
    m = ppv.merge(mine, on="sleeper_id", how="inner")
    m = m[m.games >= 4]
    diff = (m.etl_ppg - m.ppv_ppg)
    print(f"validation vs points_model (2025 Drew, {len(m)} players, ≥4 gms): "
          f"corr={m.etl_ppg.corr(m.ppv_ppg):.4f}, "
          f"median |Δppg|={diff.abs().median():.3f}, "
          f"p90 |Δppg|={diff.abs().quantile(0.9):.3f}")
    post = m[m.games > m.games_etl]
    if len(post):
        print(f"NOTE: {len(post)} players show more games in points_model than "
              f"REG season contains — points_model includes NFL POST weeks "
              f"(outcomes is REG-only by design; fantasy seasons end in REG). "
              f"Apply season_type=='REG' in points_model for consistency.")
    worst = m.assign(ad=diff.abs()).nlargest(5, "ad")
    print(worst[["sleeper_id", "ppv_ppg", "etl_ppg", "games", "games_etl"]]
          .to_string(index=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/dynasty.db")
    ap.add_argument("--seasons", nargs=2, type=int, default=[2019, 2025],
                    metavar=("FIRST", "LAST"))
    ap.add_argument("--seed-fc", action="store_true")
    args = ap.parse_args()
    seasons = list(range(args.seasons[0], args.seasons[1] + 1))

    con = sqlite3.connect(args.db)
    con.executescript(DDL)

    build_calendar(con, seasons)
    weekly = load_weekly(seasons)

    # identity: nflverse player_id IS gsis_id -> crosswalk -> sleeper_id
    xw = pd.read_sql_query(
        "SELECT gsis_id, sleeper_id FROM id_crosswalk WHERE gsis_id IS NOT NULL",
        con).drop_duplicates("gsis_id")
    weekly = weekly.merge(xw, left_on="player_id", right_on="gsis_id",
                          how="left")
    matched = weekly.sleeper_id.notna()
    # report match rate weighted by fantasy relevance, not just row count
    rel = weekly.receiving_yards.fillna(0) + weekly.rushing_yards.fillna(0) \
        + weekly.passing_yards.fillna(0)
    print(f"gsis->sleeper match: {matched.mean():.1%} of rows, "
          f"{rel[matched].sum() / max(rel.sum(), 1):.1%} of total yardage")
    weekly = weekly[matched]

    configs = canonical_configs(con)
    con.execute("DELETE FROM outcomes")
    for _, lg in configs.iterrows():
        cfg = json.loads(lg.scoring_settings_json)
        pts, unmapped = score_config(weekly, cfg)
        out = pd.DataFrame({
            "league_id": lg.league_id, "sleeper_id": weekly.sleeper_id,
            "season": weekly.season, "week": weekly.week,
            "pts": pts.round(2), "active": 1,
        }).drop_duplicates(["sleeper_id", "season", "week"])
        out.to_sql("outcomes", con, if_exists="append", index=False)
        con.execute(
            "INSERT OR REPLACE INTO outcomes_provenance VALUES "
            "(?,?,?,?,?,?,?,datetime('now'))",
            (lg.league_id, lg.league_name, int(lg.is_canonical),
             int(lg.is_best_ball), int(lg.season),
             lg.scoring_settings_json, json.dumps(sorted(unmapped))))
        flag = " [CANONICAL]" if lg.is_canonical else \
               (" [best ball]" if lg.is_best_ball else "")
        print(f"  {lg.league_name}{flag}: {len(out)} rows; "
              f"unmapped nonzero keys: {sorted(unmapped) or 'none'}")
    con.commit()

    if args.seed_fc:
        seed_fc_from_warehouse(con)
    validate_against_points_model(con)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
