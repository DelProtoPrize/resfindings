#!/usr/bin/env python3
"""
points_model.py
===============
Value-Based Drafting (VBD) valuation grounded in ACTUAL fantasy points.

Why this exists: the FantasyPros value (via DynastyProcess) is an expert *rank*
pushed through a fixed exponential curve (10500 * e^(rank*-0.0235)). That curve
imposes a smooth elite-over-starter dropoff that the real points distribution does
not have — e.g. the WR2/WR3 tier is nearly flat while RB stays scarce at the top.
This module replaces that with value-over-replacement computed from real per-game
production, scored under EACH league's exact settings (so TE-premium is intrinsic,
not a multiplier).

Pipeline:
  1. read leagues (scoring + roster slots) from the warehouse  (dim_leagues)
  2. pull nflverse weekly player stats for the target season
  3. score every player-week under each league's exact scoring_settings (incl. TEP)
  4. aggregate to PPG; derive positional replacement level from roster slots
  5. VORP = PPG - replacement_PPG; scale to a comparable value currency
  6. map nflverse gsis_id -> sleeper_id via the DynastyProcess crosswalk
  7. write player_production_value + the 3-source view v_player_value

Run (after etl_pipeline.py):
    python points_model.py                  # uses latest completed season
    POINTS_SEASON=2025 python points_model.py

No new dependencies (nflverse is read as CSV via pandas).
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

try:
    from dotenv import load_dotenv, find_dotenv
    # Windows editors/PowerShell often save .env as UTF-16 or with a BOM, which
    # the default UTF-8 reader can't decode. Detect the BOM and read accordingly.
    _env_path = find_dotenv(usecwd=True)
    _enc = "utf-8"
    if _env_path:
        _bom = open(_env_path, "rb").read(3)
        if _bom[:2] in (b"\xff\xfe", b"\xfe\xff"):
            _enc = "utf-16"
        elif _bom == b"\xef\xbb\xbf":
            _enc = "utf-8-sig"
    load_dotenv(_env_path or None, encoding=_enc)
except ImportError:  # dotenv is optional
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("points")

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
CROSSWALK = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
MIN_GAMES = int(os.getenv("POINTS_MIN_GAMES", "6"))   # qualifier for replacement ranking
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "dynasty-points-model/1.0"})

# Sleeper scoring key -> nflverse weekly stat column (per-unit scoring)
SCORING_MAP = {
    "pass_yd": "passing_yards", "pass_td": "passing_tds", "pass_int": "passing_interceptions",
    "pass_2pt": "passing_2pt_conversions",
    "rush_yd": "rushing_yards", "rush_td": "rushing_tds", "rush_2pt": "rushing_2pt_conversions",
    "rec": "receptions", "rec_yd": "receiving_yards", "rec_td": "receiving_tds",
    "rec_2pt": "receiving_2pt_conversions",
}
FUMBLE_COLS = ["rushing_fumbles_lost", "receiving_fumbles_lost", "sack_fumbles_lost"]


def get_engine() -> Engine:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        url = f"sqlite:///{(DATA_DIR / 'dynasty.db').as_posix()}"
    return create_engine(url, pool_pre_ping=True)


def load_leagues(engine: Engine) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT league_id, season, number_of_teams, scoring_settings_json, "
        "roster_positions_json FROM dim_leagues", engine)
    if df.empty or df["scoring_settings_json"].isna().all():
        raise SystemExit("dim_leagues has no scoring_settings — re-run etl_pipeline.py first.")
    return df


def target_season(leagues: pd.DataFrame) -> int:
    if os.getenv("POINTS_SEASON"):
        return int(os.getenv("POINTS_SEASON"))
    # Latest completed season = (max league season) - 1 (current season not yet played).
    return int(max(int(s) for s in leagues["season"].dropna())) - 1


def fetch_weekly(season: int) -> pd.DataFrame:
    txt = SESSION.get(NFLVERSE.format(season=season), timeout=60).text
    raw = pd.read_csv(io.StringIO(txt), low_memory=False)
    pos = "position" if "position" in raw.columns else "position_group"
    name = "player_display_name" if "player_display_name" in raw.columns else "player_name"
    pid = "player_id" if "player_id" in raw.columns else "gsis_id"
    # Build a clean frame under canonical names (nflverse ships both player_name and
    # player_display_name, so renaming in place would create duplicate columns).
    df = pd.DataFrame({
        "gsis_id": raw[pid], "player_name": raw[name],
        "position": raw[pos], "week": raw["week"],
    })
    for c in set(SCORING_MAP.values()) | set(FUMBLE_COLS) | {"receptions"}:
        if c in raw.columns:
            df[c] = pd.to_numeric(raw[c], errors="coerce").fillna(0)
    log.info("nflverse %s: %s player-weeks", season, len(df))
    return df


def fetch_bridge() -> pd.DataFrame:
    txt = SESSION.get(CROSSWALK, timeout=60).text
    xw = pd.read_csv(io.StringIO(txt), dtype=str)
    return xw[["gsis_id", "sleeper_id"]].dropna().drop_duplicates("gsis_id")


def score_weekly(df: pd.DataFrame, scoring: dict) -> pd.Series:
    """Fantasy points per player-week under one league's exact scoring (incl. TEP)."""
    def col(name: str) -> pd.Series:
        return df[name].fillna(0) if name in df.columns else pd.Series(0, index=df.index)

    pts = pd.Series(0.0, index=df.index)
    for key, stat in SCORING_MAP.items():
        if scoring.get(key):
            pts = pts + col(stat) * float(scoring[key])
    if scoring.get("fum_lost"):
        fum = sum((col(c) for c in FUMBLE_COLS), start=pd.Series(0, index=df.index))
        pts = pts + fum * float(scoring["fum_lost"])
    # TE-PREMIUM: extra per-reception bonus applied to tight ends only.
    if scoring.get("bonus_rec_te"):
        pts = pts + col("receptions") * (df["position"] == "TE") * float(scoring["bonus_rec_te"])
    return pts


def starters_per_position(roster_positions: list[str]) -> dict[str, float]:
    """Translate roster slots into expected starters per position. FLEX is split
    across RB/WR/TE; SUPER_FLEX is treated as a QB slot (how it's used in practice)."""
    ded = {p: roster_positions.count(p) for p in ("QB", "RB", "WR", "TE")}
    flex = sum(roster_positions.count(s) for s in ("FLEX", "WRRB_FLEX", "REC_FLEX", "WRRBTE_FLEX"))
    sflex = roster_positions.count("SUPER_FLEX")
    return {
        "QB": ded["QB"] + sflex,
        "RB": ded["RB"] + flex / 3,
        "WR": ded["WR"] + flex / 3,
        "TE": ded["TE"] + flex / 3,
    }


def compute_vorp(weekly: pd.DataFrame, scoring: dict, roster_positions: list[str],
                 teams: int) -> pd.DataFrame:
    """PPG + replacement + VORP for one league's scoring profile."""
    w = weekly.assign(pts=score_weekly(weekly, scoring))
    w = w[w["position"].isin(["QB", "RB", "WR", "TE"])]
    agg = (w.groupby(["gsis_id", "player_name", "position"])
             .agg(games=("week", "nunique"), ppg=("pts", "mean"))
             .reset_index())

    starters = starters_per_position(roster_positions)
    out = []
    for pos, grp in agg.groupby("position"):
        qualified = grp[grp["games"] >= MIN_GAMES].sort_values("ppg", ascending=False).reset_index(drop=True)
        repl_rank = max(1, round(teams * starters.get(pos, 0)))
        idx = min(repl_rank - 1, len(qualified) - 1)
        repl = float(qualified.loc[idx, "ppg"]) if len(qualified) else 0.0
        grp = grp.assign(replacement_ppg=repl, vorp=(grp["ppg"] - repl).clip(lower=0))
        out.append(grp)
    res = pd.concat(out, ignore_index=True)

    # Scale VORP to a value currency roughly comparable to FP/FC (top asset ~10000).
    top = res["vorp"].max()
    res["vbd_value"] = (res["vorp"] / top * 10000).round(0) if top else 0
    return res


def run() -> None:
    engine = get_engine()
    leagues = load_leagues(engine)
    season = target_season(leagues)
    log.info("Grounding value in %s actuals (min %s games for replacement)", season, MIN_GAMES)

    weekly = fetch_weekly(season)
    bridge = fetch_bridge()

    rows = []
    for lg in leagues.itertuples():
        scoring = json.loads(lg.scoring_settings_json or "{}")
        rp = json.loads(lg.roster_positions_json or "[]")
        if not scoring or not rp:
            continue
        vorp = compute_vorp(weekly, scoring, rp, int(lg.number_of_teams or 14))
        vorp = vorp.merge(bridge, on="gsis_id", how="inner")        # gsis -> sleeper
        vorp = vorp.assign(season=season, league_id=lg.league_id)
        rows.append(vorp.rename(columns={"sleeper_id": "player_id"})[[
            "season", "league_id", "player_id", "position", "games",
            "ppg", "replacement_ppg", "vorp", "vbd_value"]])
    if not rows:
        raise SystemExit("No leagues had scoring/roster settings to score.")
    out = pd.concat(rows, ignore_index=True)
    out[["ppg", "replacement_ppg", "vorp"]] = out[["ppg", "replacement_ppg", "vorp"]].round(2)
    log.info("Computed VBD for %s player-league rows", len(out))

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS player_production_value (
                season          INTEGER NOT NULL,
                league_id       TEXT    NOT NULL,
                player_id       TEXT    NOT NULL,
                position        TEXT,
                games           INTEGER,
                ppg             NUMERIC,
                replacement_ppg NUMERIC,
                vorp            NUMERIC,
                vbd_value       NUMERIC,
                PRIMARY KEY (season, league_id, player_id)
            )"""))
        conn.execute(text("DELETE FROM player_production_value WHERE season = :s"), {"s": season})
        out.to_sql("player_production_value", conn, if_exists="append", index=False)

        # Three-source view: expert (FP), market (FC), production (VBD), side by side.
        conn.execute(text("DROP VIEW IF EXISTS v_player_value"))
        conn.execute(text("""
            CREATE VIEW v_player_value AS
            SELECT pm.snapshot_date, pm.league_id, pm.roster_id, pm.player_id,
                   pm.player_name, pm.position, pm.age,
                   pm.fp_market_value, pm.fc_market_value, pm.arb_delta_fp_minus_fc,
                   pv.ppg, pv.vorp, pv.vbd_value
            FROM v_player_market pm
            LEFT JOIN player_production_value pv
              ON pv.league_id = pm.league_id AND pv.player_id = pm.player_id
             AND pv.season = (SELECT MAX(season) FROM player_production_value)"""))
    log.info("Wrote player_production_value + v_player_value for season %s", season)


if __name__ == "__main__":
    run()
