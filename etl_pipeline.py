#!/usr/bin/env python3
"""
etl_pipeline.py
===============
Phase 1 of the "Roster Portfolio & Asset Valuation" data product.

Extracts Sleeper league data + external dynasty market values, normalizes player
identities across sources via the DynastyProcess crosswalk, and loads a clean
star schema into PostgreSQL.

Design note (read this in your interview):
    There is NO free public historical time-series of dynasty market values
    (KTC has no API and 503s server requests; FantasyCalc's historical endpoint
    is gone; DynastyProcess publishes only the latest scrape). Therefore the
    fact table is a SNAPSHOT fact partitioned by `snapshot_date`: every run
    appends today's market values idempotently (ON CONFLICT upsert), accruing a
    true longitudinal valuation series going FORWARD. Roster/transaction history,
    by contrast, IS fully backfillable from Sleeper via `previous_league_id`
    chaining, which this script does.

Sources (all verified reachable June 2026):
    Sleeper REST API ............. https://api.sleeper.app/v1/...
    FantasyCalc current values ... https://api.fantasycalc.com/values/current
    DynastyProcess values ........ raw.githubusercontent.com/dynastyprocess/data
    DynastyProcess crosswalk ..... db_playerids.csv (sleeper_id <-> ktc_id <-> fp_id)

Requirements (see requirements.txt):
    requests, pandas, SQLAlchemy>=2.0, python-dotenv

Configuration (environment / .env):
    SLEEPER_USERNAME=your_sleeper_handle      # OR set SLEEPER_USER_ID directly
    SLEEPER_USER_ID=                          # optional, skips username lookup
    SLEEPER_SEASON=2026
    LEAGUE_ID_FILTER=                         # optional CSV of league_ids to limit to
    BACKFILL_PREVIOUS_SEASONS=true            # walk previous_league_id for history
    DATABASE_URL=                             # optional; defaults to sqlite:///<DATA_DIR>/dynasty.db
    PLAYER_CACHE_TTL_HOURS=24
    DATA_DIR=./data

Run:
    python etl_pipeline.py            # full run
    python etl_pipeline.py --dry-run  # extract + transform, skip DB load
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable

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

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

SLEEPER_BASE = "https://api.sleeper.app/v1"
FANTASYCALC_BASE = "https://api.fantasycalc.com"
DP_BASE = "https://raw.githubusercontent.com/dynastyprocess/data/master/files"

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
PLAYER_CACHE_TTL_HOURS = int(os.getenv("PLAYER_CACHE_TTL_HOURS", "24"))
SNAPSHOT_DATE = datetime.now(timezone.utc).date()

USER_AGENT = "dynasty-portfolio-etl/1.0 (+analytics-portfolio-project)"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(DATA_DIR / "etl.log") if DATA_DIR.exists() else logging.NullHandler(),
    ],
)
log = logging.getLogger("etl")


# --------------------------------------------------------------------------- #
# Defensive HTTP layer: shared session, rate limiting, retry/backoff
# --------------------------------------------------------------------------- #

class RateLimiter:
    """Simple min-interval limiter. Sleeper tolerates ~1000 calls/min; we stay
    well under that with a conservative default of ~8 req/s."""

    def __init__(self, min_interval_s: float = 0.12) -> None:
        self.min_interval = min_interval_s
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        gap = now - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()


SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
SLEEPER_LIMITER = RateLimiter(0.12)
EXTERNAL_LIMITER = RateLimiter(0.5)  # be gentler with third-party hosts

RETRYABLE = {429, 500, 502, 503, 504}


def http_get(
    url: str,
    limiter: RateLimiter,
    *,
    params: dict | None = None,
    expect: str = "json",
    max_retries: int = 5,
    timeout: int = 30,
) -> Any | None:
    """GET with exponential backoff + jitter. Returns None on a clean 404 so
    callers can treat 'not found' as empty rather than fatal."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        limiter.wait()
        try:
            resp = SESSION.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            _sleep_backoff(attempt)
            continue

        if resp.status_code == 200:
            return resp.json() if expect == "json" else resp.text
        if resp.status_code == 404:
            return None
        if resp.status_code in RETRYABLE:
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                time.sleep(min(60, int(retry_after)))
            else:
                _sleep_backoff(attempt)
            log.warning("Retry %s/%s on %s (HTTP %s)", attempt + 1, max_retries, url, resp.status_code)
            continue
        resp.raise_for_status()

    raise RuntimeError(f"GET failed after {max_retries} retries: {url} ({last_exc})")


def _sleep_backoff(attempt: int, base: float = 1.5, cap: float = 45.0) -> None:
    time.sleep(min(cap, base ** attempt) + random.uniform(0, 0.75))


# --------------------------------------------------------------------------- #
# Sleeper extraction
# --------------------------------------------------------------------------- #

def resolve_user_id() -> dict:
    uid = os.getenv("SLEEPER_USER_ID", "").strip()
    if uid:
        data = http_get(f"{SLEEPER_BASE}/user/{uid}", SLEEPER_LIMITER)
    else:
        username = os.getenv("SLEEPER_USERNAME", "").strip()
        if not username:
            raise SystemExit("Set SLEEPER_USERNAME or SLEEPER_USER_ID in your environment / .env")
        data = http_get(f"{SLEEPER_BASE}/user/{username}", SLEEPER_LIMITER)
    if not data:
        raise SystemExit("Sleeper user not found — check SLEEPER_USERNAME/SLEEPER_USER_ID")
    log.info("Resolved Sleeper user: %s (%s)", data.get("display_name"), data["user_id"])
    return data


def get_player_db() -> dict[str, dict]:
    """The /players/nfl payload is ~15 MB; cache to disk with a TTL so we don't
    re-pull it every run (Sleeper explicitly asks callers not to)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache = DATA_DIR / "sleeper_players.json"
    if cache.exists():
        age_h = (time.time() - cache.stat().st_mtime) / 3600
        if age_h < PLAYER_CACHE_TTL_HOURS:
            log.info("Using cached player DB (%.1f h old)", age_h)
            return pd.read_json(cache, typ="series").to_dict()
    log.info("Fetching Sleeper player DB (~15 MB)...")
    data = http_get(f"{SLEEPER_BASE}/players/nfl", SLEEPER_LIMITER)
    cache.write_text(pd.Series(data).to_json())
    log.info("Player DB cached: %s players", len(data))
    return data


def get_user_leagues(user_id: str, season: str) -> list[dict]:
    leagues = http_get(f"{SLEEPER_BASE}/user/{user_id}/leagues/nfl/{season}", SLEEPER_LIMITER) or []
    flt = os.getenv("LEAGUE_ID_FILTER", "").strip()
    if flt:
        wanted = {x.strip() for x in flt.split(",")}
        leagues = [lg for lg in leagues if lg["league_id"] in wanted]
    log.info("Found %s league(s) for season %s", len(leagues), season)
    return leagues


def walk_league_history(league: dict) -> list[dict]:
    """Follow previous_league_id back through prior seasons. Sleeper retains the
    full chain — this is how we backfill roster/transaction history."""
    chain = [league]
    if os.getenv("BACKFILL_PREVIOUS_SEASONS", "true").lower() != "true":
        return chain
    prev = league.get("previous_league_id")
    while prev and prev != "0":
        node = http_get(f"{SLEEPER_BASE}/league/{prev}", SLEEPER_LIMITER)
        if not node:
            break
        chain.append(node)
        prev = node.get("previous_league_id")
    if len(chain) > 1:
        log.info("League '%s' history chain: %s seasons", league.get("name"), len(chain))
    return chain


def parse_league_settings(league: dict) -> dict:
    rp = league.get("roster_positions", []) or []
    scoring = league.get("scoring_settings", {}) or {}
    is_superflex = "SUPER_FLEX" in rp or rp.count("QB") >= 2
    te_premium_bonus = float(scoring.get("bonus_rec_te", 0) or 0)
    return {
        "league_id": league["league_id"],
        "league_name": league.get("name"),
        "season": league.get("season"),
        "number_of_teams": int(league.get("total_rosters") or len(rp) or 0),
        "is_superflex": is_superflex,
        "te_premium_value": te_premium_bonus,
        "ppr": float(scoring.get("rec", 0) or 0),
        "previous_league_id": league.get("previous_league_id"),
    }


def get_rosters(league_id: str) -> list[dict]:
    return http_get(f"{SLEEPER_BASE}/league/{league_id}/rosters", SLEEPER_LIMITER) or []


def get_league_users(league_id: str) -> list[dict]:
    return http_get(f"{SLEEPER_BASE}/league/{league_id}/users", SLEEPER_LIMITER) or []


def get_traded_picks(league_id: str) -> list[dict]:
    return http_get(f"{SLEEPER_BASE}/league/{league_id}/traded_picks", SLEEPER_LIMITER) or []


def get_transactions(league_id: str, weeks: Iterable[int]) -> list[dict]:
    out: list[dict] = []
    for wk in weeks:
        rows = http_get(f"{SLEEPER_BASE}/league/{league_id}/transactions/{wk}", SLEEPER_LIMITER) or []
        for r in rows:
            r["_week"] = wk
        out.extend(rows)
    return out


def get_matchups(league_id: str, weeks: Iterable[int]) -> list[dict]:
    out: list[dict] = []
    for wk in weeks:
        rows = http_get(f"{SLEEPER_BASE}/league/{league_id}/matchups/{wk}", SLEEPER_LIMITER) or []
        for r in rows:
            r["_week"] = wk
        out.extend(rows)
    return out


# --------------------------------------------------------------------------- #
# Market value extraction
# --------------------------------------------------------------------------- #

def fetch_crosswalk() -> pd.DataFrame:
    """DynastyProcess db_playerids.csv: sleeper_id <-> ktc_id <-> fantasypros_id."""
    txt = http_get(f"{DP_BASE}/db_playerids.csv", EXTERNAL_LIMITER, expect="text")
    df = pd.read_csv(io.StringIO(txt), dtype=str)
    keep = ["sleeper_id", "ktc_id", "fantasypros_id", "mfl_id", "name", "merge_name", "position", "team"]
    df = df[[c for c in keep if c in df.columns]].copy()
    log.info("Crosswalk loaded: %s rows", len(df))
    return df


def fetch_dynastyprocess_values() -> pd.DataFrame:
    """Player-level SF/1QB values. value_2qb = Superflex market value."""
    txt = http_get(f"{DP_BASE}/values-players.csv", EXTERNAL_LIMITER, expect="text")
    df = pd.read_csv(io.StringIO(txt))
    df = df.rename(columns={"fp_id": "fantasypros_id"})
    df["fantasypros_id"] = df["fantasypros_id"].astype(str)
    return df[["fantasypros_id", "player", "value_1qb", "value_2qb", "ecr_2qb", "scrape_date"]]


def fetch_fantasycalc(num_qbs: int, num_teams: int, ppr: float) -> pd.DataFrame:
    """FantasyCalc current values; each record carries sleeperId for a clean join.
    Returns players AND draft picks (picks come back with position == 'PICK')."""
    params = {"isDynasty": "true", "numQbs": num_qbs, "numTeams": num_teams, "ppr": max(ppr, 0)}
    data = http_get(f"{FANTASYCALC_BASE}/values/current", EXTERNAL_LIMITER, params=params) or []
    rows = []
    for rec in data:
        p = rec.get("player", {}) or {}
        rows.append({
            "sleeper_id": p.get("sleeperId"),
            "mfl_id": p.get("mflId"),
            "fc_name": p.get("name"),
            "position": p.get("position"),
            "fc_value": rec.get("value"),
            "fc_redraft_value": rec.get("redraftValue"),
            "fc_overall_rank": rec.get("overallRank"),
            "fc_position_rank": rec.get("positionRank"),
            "fc_trend_30day": rec.get("trend30Day"),
            "fc_adp": rec.get("maybeAdp"),
        })
    df = pd.DataFrame(rows)
    log.info("FantasyCalc: %s assets (numQbs=%s, numTeams=%s)", len(df), num_qbs, num_teams)
    return df


# --------------------------------------------------------------------------- #
# Normalization / crosswalk join
# --------------------------------------------------------------------------- #

def normalize_market_values(
    fc_by_format: dict[int, pd.DataFrame],
    dp: pd.DataFrame,
    crosswalk: pd.DataFrame,
    player_db: dict[str, dict],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Produce one row per sleeper_id with the PRIMARY (DynastyProcess FantasyPros
    ECR) values plus the SECONDARY (FantasyCalc) cross-check.

    PRIMARY  : DynastyProcess values-players.csv -> fp_value_1qb / fp_value_2qb /
               fp_ecr_2qb. Keyed on fantasypros_id, mapped to sleeper_id via the
               crosswalk. This is the valuation the dashboard runs on.
    SECONDARY: FantasyCalc current values, pulled once per QB format the user's
               leagues need (1 and/or 2). Already keyed on sleeper_id (direct
               join). Demoted to a cross-check; the FP-vs-FC gap is the arbitrage
               signal in Phase 3.

    Both 1QB and SF values are stored so each league resolves its own format in
    the Tableau semantic layer. Unmatched DP rows are logged to unmatched_players.csv.
    """
    # ---- PRIMARY: DP FantasyPros ECR -> sleeper_id via crosswalk ----
    dp_mapped = dp.merge(
        crosswalk[["fantasypros_id", "sleeper_id"]].dropna(),
        on="fantasypros_id", how="left",
    )
    unmatched_dp = dp_mapped[dp_mapped["sleeper_id"].isna()][["player", "fantasypros_id"]]
    dp_mapped = dp_mapped.dropna(subset=["sleeper_id"]).copy()
    dp_mapped["sleeper_id"] = dp_mapped["sleeper_id"].astype(str)
    primary = dp_mapped.rename(columns={
        "value_1qb": "fp_value_1qb",
        "value_2qb": "fp_value_2qb",
        "ecr_2qb": "fp_ecr_2qb",
    })[["sleeper_id", "fp_value_1qb", "fp_value_2qb", "fp_ecr_2qb"]]

    # ---- SECONDARY: FantasyCalc, one column per format ----
    merged = primary
    adp_trend = None
    for num_qbs, fc in fc_by_format.items():
        players = fc[fc["position"] != "PICK"].dropna(subset=["sleeper_id"]).copy()
        players["sleeper_id"] = players["sleeper_id"].astype(str)
        col = f"fc_value_{num_qbs}qb"
        merged = merged.merge(
            players[["sleeper_id", "fc_value"]].rename(columns={"fc_value": col}),
            on="sleeper_id", how="outer",
        )
        # ADP / 30-day trend are format-agnostic enough; keep one copy
        if adp_trend is None:
            adp_trend = players[["sleeper_id", "fc_adp", "fc_trend_30day"]]
    if adp_trend is not None:
        merged = merged.merge(adp_trend, on="sleeper_id", how="left")

    # ---- Enrich names/positions from the authoritative Sleeper player DB ----
    def enrich(sid: str, col: str, default=None):
        rec = player_db.get(str(sid)) or {}
        return rec.get(col, default)

    merged["player_name"] = merged["sleeper_id"].map(lambda s: enrich(s, "full_name"))
    merged["position"] = merged["sleeper_id"].map(lambda s: enrich(s, "position"))

    if len(unmatched_dp):
        path = DATA_DIR / "unmatched_players.csv"
        unmatched_dp.to_csv(path, index=False)
        log.warning("Unmatched DP players: %s -> %s", len(unmatched_dp), path)

    # Picks come from whichever FC format we pulled (prefer SF if present)
    fc_src = fc_by_format.get(2) if 2 in fc_by_format else next(iter(fc_by_format.values()))
    picks = fc_src[fc_src["position"] == "PICK"].copy()
    return merged, picks


# --------------------------------------------------------------------------- #
# Transform: build star-schema frames
# --------------------------------------------------------------------------- #

@dataclass
class Frames:
    dim_leagues: pd.DataFrame = field(default_factory=pd.DataFrame)
    dim_managers: pd.DataFrame = field(default_factory=pd.DataFrame)
    dim_players: pd.DataFrame = field(default_factory=pd.DataFrame)
    dim_draft_picks: pd.DataFrame = field(default_factory=pd.DataFrame)
    fact_roster_value: pd.DataFrame = field(default_factory=pd.DataFrame)


def build_frames(
    leagues_meta: list[dict],
    managers: list[dict],
    rosters_by_league: dict[str, list[dict]],
    market: pd.DataFrame,
    player_db: dict[str, dict],
    traded_picks: list[dict],
) -> Frames:
    f = Frames()
    f.dim_leagues = pd.DataFrame(leagues_meta)
    f.dim_managers = pd.DataFrame(managers).drop_duplicates(subset=["roster_id", "league_id"])

    # Dim_Players from the Sleeper DB, restricted to rostered players for compactness
    rostered = {
        str(pid)
        for rs in rosters_by_league.values()
        for r in rs
        for pid in (r.get("players") or [])
    }
    prows = []
    for pid in rostered:
        rec = player_db.get(pid, {})
        prows.append({
            "player_id": pid,
            "player_name": rec.get("full_name"),
            "position": rec.get("position"),
            "age": rec.get("age"),
            "nfl_team": rec.get("team"),
            "years_exp": rec.get("years_exp"),
            "is_rookie": (rec.get("years_exp") == 0),
        })
    f.dim_players = pd.DataFrame(prows)

    # Dim_Draft_Picks (current ownership from traded_picks)
    f.dim_draft_picks = pd.DataFrame([
        {
            "pick_id": f"{tp['season']}-R{tp['round']}-orig{tp['roster_id']}-{lg}",
            "league_id": lg,
            "year": tp.get("season"),
            "round": tp.get("round"),
            "original_owner_id": tp.get("roster_id"),
            "current_owner_id": tp.get("owner_id"),
            "previous_owner_id": tp.get("previous_owner_id"),
        }
        for lg, rows in traded_picks_by_league(traded_picks).items()
        for tp in rows
    ]) if traded_picks else pd.DataFrame()

    # Fact: one row per (snapshot_date, league, roster, player)
    mv = market.set_index("sleeper_id")
    fact_rows = []
    for lg, rs in rosters_by_league.items():
        for r in rs:
            rid = r["roster_id"]
            for pid in (r.get("players") or []):
                row = mv.loc[str(pid)] if str(pid) in mv.index else None
                g = (lambda c: None) if row is None else (lambda c: (None if pd.isna(row.get(c)) else row.get(c)))
                fact_rows.append({
                    "snapshot_date": SNAPSHOT_DATE,
                    "league_id": lg,
                    "roster_id": rid,
                    "player_id": str(pid),
                    # PRIMARY: FantasyPros ECR (DynastyProcess)
                    "fp_value_1qb": g("fp_value_1qb"),
                    "fp_value_2qb": g("fp_value_2qb"),
                    "fp_ecr_2qb": g("fp_ecr_2qb"),
                    # SECONDARY: FantasyCalc cross-check (per format)
                    "fc_value_1qb": g("fc_value_1qb"),
                    "fc_value_2qb": g("fc_value_2qb"),
                    "sleeper_adp_value": g("fc_adp"),
                    "fc_trend_30day": g("fc_trend_30day"),
                })
    f.fact_roster_value = pd.DataFrame(fact_rows)
    return f


def traded_picks_by_league(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r.get("league_id", "unknown"), []).append(r)
    return out


# --------------------------------------------------------------------------- #
# Load: SQLite (default) via SQLAlchemy, idempotent upsert.
# SQLite (>=3.24) and PostgreSQL share the INSERT ... ON CONFLICT DO UPDATE
# syntax used below, so the loader is portable across both with no code change.
# --------------------------------------------------------------------------- #

DDL = """
CREATE TABLE IF NOT EXISTS dim_leagues (
    league_id           TEXT PRIMARY KEY,
    league_name         TEXT,
    season              TEXT,
    number_of_teams     INT,
    is_superflex        BOOLEAN,
    te_premium_value    NUMERIC,
    ppr                 NUMERIC,
    previous_league_id  TEXT
);
CREATE TABLE IF NOT EXISTS dim_managers (
    roster_id        INT,
    league_id        TEXT REFERENCES dim_leagues(league_id),
    sleeper_user_id  TEXT,
    sleeper_username TEXT,
    owner_name       TEXT,
    PRIMARY KEY (league_id, roster_id)
);
CREATE TABLE IF NOT EXISTS dim_players (
    player_id   TEXT PRIMARY KEY,
    player_name TEXT,
    position    TEXT,
    age         NUMERIC,
    nfl_team    TEXT,
    years_exp   INT,
    is_rookie   BOOLEAN
);
CREATE TABLE IF NOT EXISTS dim_draft_picks (
    pick_id            TEXT PRIMARY KEY,
    league_id          TEXT,
    year               TEXT,
    round              INT,
    original_owner_id  INT,
    current_owner_id   INT,
    previous_owner_id  INT
);
CREATE TABLE IF NOT EXISTS fact_roster_historical_value (
    snapshot_date      DATE    NOT NULL,
    league_id          TEXT    NOT NULL,
    roster_id          INT     NOT NULL,
    player_id          TEXT    NOT NULL,
    fp_value_1qb       NUMERIC,   -- PRIMARY: FantasyPros ECR value, 1QB
    fp_value_2qb       NUMERIC,   -- PRIMARY: FantasyPros ECR value, Superflex
    fp_ecr_2qb         NUMERIC,   -- PRIMARY: FantasyPros consensus rank, Superflex
    fc_value_1qb       NUMERIC,   -- SECONDARY: FantasyCalc cross-check, 1QB
    fc_value_2qb       NUMERIC,   -- SECONDARY: FantasyCalc cross-check, Superflex
    sleeper_adp_value  NUMERIC,
    fc_trend_30day     NUMERIC,
    PRIMARY KEY (snapshot_date, league_id, roster_id, player_id)
);
CREATE INDEX IF NOT EXISTS ix_fact_player ON fact_roster_historical_value(player_id);
CREATE INDEX IF NOT EXISTS ix_fact_league_date ON fact_roster_historical_value(league_id, snapshot_date);
"""


def get_engine() -> Engine:
    """Defaults to a local SQLite file (portable, zero-config). Override with
    DATABASE_URL for any other SQLAlchemy-supported backend."""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{(DATA_DIR / 'dynasty.db').as_posix()}"
        log.info("DATABASE_URL not set — using %s", url)
    return create_engine(url, pool_pre_ping=True)


def upsert(engine: Engine, table: str, df: pd.DataFrame, conflict_cols: list[str]) -> None:
    """Idempotent INSERT ... ON CONFLICT DO UPDATE via a staging table."""
    if df.empty:
        log.info("Skip %s (no rows)", table)
        return
    df = df.where(pd.notna(df), None)
    staging = f"_stg_{table}"
    with engine.begin() as conn:
        df.to_sql(staging, conn, if_exists="replace", index=False)
        cols = list(df.columns)
        collist = ", ".join(f'"{c}"' for c in cols)
        updates = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in cols if c not in conflict_cols)
        conflict = ", ".join(f'"{c}"' for c in conflict_cols)
        action = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
        conn.execute(text(
            f'INSERT INTO {table} ({collist}) SELECT {collist} FROM {staging} '
            f'WHERE true '  # disambiguates SELECT from the upsert clause in SQLite; valid in PostgreSQL too
            f'ON CONFLICT ({conflict}) {action}'
        ))
        conn.execute(text(f"DROP TABLE IF EXISTS {staging}"))
    log.info("Upserted %s rows into %s", len(df), table)


def load(engine: Engine, frames: Frames) -> None:
    with engine.begin() as conn:
        for stmt in filter(None, (s.strip() for s in DDL.split(";"))):
            conn.execute(text(stmt))
    upsert(engine, "dim_leagues", frames.dim_leagues, ["league_id"])
    upsert(engine, "dim_managers", frames.dim_managers, ["league_id", "roster_id"])
    upsert(engine, "dim_players", frames.dim_players, ["player_id"])
    if not frames.dim_draft_picks.empty:
        upsert(engine, "dim_draft_picks", frames.dim_draft_picks, ["pick_id"])
    upsert(engine, "fact_roster_historical_value", frames.fact_roster_value,
           ["snapshot_date", "league_id", "roster_id", "player_id"])


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def run(dry_run: bool = False) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    season = os.getenv("SLEEPER_SEASON", str(SNAPSHOT_DATE.year))

    user = resolve_user_id()
    player_db = get_player_db()
    crosswalk = fetch_crosswalk()
    dp_values = fetch_dynastyprocess_values()

    leagues = get_user_leagues(user["user_id"], season)
    if not leagues:
        raise SystemExit(f"No leagues found for {user['display_name']} in {season}")

    leagues_meta: list[dict] = []
    managers: list[dict] = []
    rosters_by_league: dict[str, list[dict]] = {}
    all_traded_picks: list[dict] = []

    for lg in leagues:
        for node in walk_league_history(lg):  # current + prior seasons
            meta = parse_league_settings(node)
            leagues_meta.append(meta)
            lid = meta["league_id"]

            rosters = get_rosters(lid)
            rosters_by_league[lid] = rosters

            users = {u["user_id"]: u for u in get_league_users(lid)}
            for r in rosters:
                u = users.get(r.get("owner_id"), {})
                managers.append({
                    "roster_id": r["roster_id"],
                    "league_id": lid,
                    "sleeper_user_id": r.get("owner_id"),
                    "sleeper_username": u.get("display_name"),
                    "owner_name": (u.get("metadata") or {}).get("team_name") or u.get("display_name"),
                })

            tp = get_traded_picks(lid)
            for t in tp:
                t["league_id"] = lid
            all_traded_picks.extend(tp)

    # Market values: PRIMARY = FantasyPros ECR (DynastyProcess, both formats in one
    # file). SECONDARY = FantasyCalc, pulled once per QB format the leagues need.
    teams = parse_league_settings(leagues[0])["number_of_teams"] or 14
    formats_needed = {2 if parse_league_settings(l)["is_superflex"] else 1 for l in leagues}
    fc_by_format = {q: fetch_fantasycalc(q, teams, ppr=1) for q in sorted(formats_needed)}
    market, picks = normalize_market_values(fc_by_format, dp_values, crosswalk, player_db)

    fp_cov = market["fp_value_2qb"].notna().mean() if len(market) else 0
    log.info("Market table: %s assets | FantasyPros (primary) coverage %.1f%%", len(market), 100 * fp_cov)

    frames = build_frames(leagues_meta, managers, rosters_by_league, market, player_db, all_traded_picks)

    if dry_run:
        log.info("DRY RUN — frames built, skipping DB load")
        for name, df in vars(frames).items():
            log.info("  %-26s %s rows", name, len(df))
        return

    engine = get_engine()
    load(engine, frames)
    log.info("ETL complete for snapshot_date=%s", SNAPSHOT_DATE)


def main() -> None:
    ap = argparse.ArgumentParser(description="Dynasty portfolio ETL")
    ap.add_argument("--dry-run", action="store_true", help="extract + transform, skip DB load")
    args = ap.parse_args()
    try:
        run(dry_run=args.dry_run)
    except KeyboardInterrupt:
        log.warning("Interrupted")
        sys.exit(130)


if __name__ == "__main__":
    main()
