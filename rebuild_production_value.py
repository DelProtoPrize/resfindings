"""
rebuild_production_value.py — fix the POST-week contamination in the VBD layer.

WHAT WAS WRONG: points_model.py did not filter season_type, so NFL playoff
weeks leaked into ppg/games. Only playoff-team players have those weeks, so
the bias is asymmetric: it inflates production for players on good teams, and
flows into replacement levels and VORP. (Found by the harness validation:
corr 0.9982 vs outcomes, but 162/503 players with games > REG max; e.g.
K.Walker 17 REG + 3 POST = 20.)

WHAT THIS PRESERVES (reverse-engineered from the legacy table, verified):
  ppg             = mean weekly pts, games = week count   [now REG-only]
  replacement_ppg = ppg of the K-th ranked player at the position, K inferred
                    PER LEAGUE from the legacy table itself (e.g. Drew:
                    QB31/RB46/WR44/TE29 — SF league, hence deep QB)
  vorp            = round(max(ppg - replacement_ppg, 0), 2)
  vbd_value       = round(vorp / max(vorp in league) * 10000)

WHAT CHANGES BESIDES REG-ONLY (disclosed, not silent):
  - Pool = all crosswalk-matched QB/RB/WR/TE with >=1 REG week (~1000),
    vs legacy's 601 (its exact pool rule isn't recoverable — 179 of its
    players aren't even in dim_players). Replacement levels can shift from
    the pool change as well as the REG fix; the delta report prints both
    old/new replacement_ppg per league-position so nothing moves silently.
  - Each league_id is scored under ITS OWN season's config (as legacy did),
    not the family-canonical config — Ballers/Punt drifted scoring across
    seasons, so this matters for their historical league rows.

Legacy table is preserved as player_production_value_legacy.

ALSO PATCH YOUR points_model.py at the nflverse read (the actual root fix):
    df = df[df["season_type"] == "REG"]

Usage:  python rebuild_production_value.py --db data/dynasty.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

from outcomes_etl import STATS_URL, POSITIONS, score_config


def infer_replacement_ranks(con: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Per league_id, per position: which ppg-rank the legacy replacement
    level corresponds to. Inferred from the legacy table so the rebuild
    preserves the original design parameter exactly."""
    ppv = pd.read_sql_query(
        "SELECT league_id, position, ppg, replacement_ppg "
        "FROM player_production_value_legacy", con)
    ks: dict[str, dict[str, int]] = {}
    for (lid, pos), g in ppv.groupby(["league_id", "position"]):
        g = g.sort_values("ppg", ascending=False).reset_index(drop=True)
        k = int((g.ppg - g.replacement_ppg.iloc[0]).abs().idxmin()) + 1
        ks.setdefault(lid, {})[pos] = k
    return ks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/dynasty.db")
    ap.add_argument("--season", type=int, default=2025)
    args = ap.parse_args()
    con = sqlite3.connect(args.db)

    # backup once; re-runs keep the original legacy snapshot
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "player_production_value_legacy" not in have:
        con.execute("ALTER TABLE player_production_value "
                    "RENAME TO player_production_value_legacy")
        con.commit()
    ks = infer_replacement_ranks(con)

    # REG-only weekly stats, crosswalked to sleeper ids — THE fix
    weekly = pd.read_csv(STATS_URL.format(season=args.season), low_memory=False)
    weekly = weekly[(weekly.season_type == "REG")
                    & (weekly.position.isin(POSITIONS))]
    weekly["is_te"] = (weekly.position == "TE").astype(float)
    xw = pd.read_sql_query(
        "SELECT gsis_id, sleeper_id FROM id_crosswalk "
        "WHERE gsis_id IS NOT NULL", con).drop_duplicates("gsis_id")
    weekly = weekly.merge(xw, left_on="player_id", right_on="gsis_id",
                          how="inner")

    leagues = pd.read_sql_query(
        "SELECT league_id, league_name, scoring_settings_json "
        "FROM dim_leagues", con)
    con.execute("DROP TABLE IF EXISTS player_production_value")
    con.execute("""CREATE TABLE player_production_value (
        season INTEGER, league_id TEXT, player_id TEXT, position TEXT,
        games INTEGER, ppg REAL, replacement_ppg REAL, vorp REAL,
        vbd_value INTEGER,
        PRIMARY KEY (season, league_id, player_id))""")

    deltas = []
    for _, lg in leagues.iterrows():
        cfg = json.loads(lg.scoring_settings_json)
        pts, _ = score_config(weekly, cfg)
        df = pd.DataFrame({"player_id": weekly.sleeper_id,
                           "position": weekly.position, "pts": pts})
        agg = (df.groupby(["player_id", "position"], as_index=False)
                 .agg(games=("pts", "size"), total=("pts", "sum")))
        agg["ppg"] = (agg.total / agg.games).round(2)

        rows = []
        for pos, g in agg.groupby("position"):
            g = g.sort_values("ppg", ascending=False).reset_index(drop=True)
            k = ks.get(lg.league_id, {}).get(pos)
            if k is None:
                continue
            rep = float(g.ppg.iloc[min(k, len(g)) - 1])
            g["replacement_ppg"] = rep
            g["vorp"] = np.maximum(g.ppg - rep, 0).round(2)
            rows.append(g)
            old = con.execute(
                "SELECT replacement_ppg FROM player_production_value_legacy "
                "WHERE league_id=? AND position=? LIMIT 1",
                (lg.league_id, pos)).fetchone()
            deltas.append((lg.league_name, lg.league_id[-6:], pos,
                           old[0] if old else None, rep))
        out = pd.concat(rows, ignore_index=True)
        mx = out.vorp.max()
        out["vbd_value"] = ((out.vorp / mx * 10000).round().astype(int)
                            if mx > 0 else 0)
        out["season"] = args.season
        out["league_id"] = lg.league_id
        out[["season", "league_id", "player_id", "position", "games", "ppg",
             "replacement_ppg", "vorp", "vbd_value"]].to_sql(
            "player_production_value", con, if_exists="append", index=False)
    con.commit()

    # ---- delta report ---------------------------------------------------------
    print("replacement_ppg shifts (legacy -> rebuilt), Drew League:")
    for nm, lid, pos, old, new in deltas:
        if nm == "The Drew League":
            print(f"  {pos}: {old:.2f} -> {new:.2f}  (Δ {new - old:+.2f})")
    legacy = pd.read_sql_query(
        "SELECT player_id, position, games AS games_old, ppg AS ppg_old, "
        "vorp AS vorp_old FROM player_production_value_legacy "
        "WHERE league_id=(SELECT league_id FROM outcomes_provenance "
        "WHERE is_canonical=1)", con)
    new = pd.read_sql_query(
        "SELECT player_id, games AS games_new, ppg AS ppg_new, vorp AS "
        "vorp_new FROM player_production_value WHERE league_id="
        "(SELECT league_id FROM outcomes_provenance WHERE is_canonical=1)",
        con)
    m = legacy.merge(new, on="player_id")
    m["dvorp"] = m.vorp_new - m.vorp_old
    movers = m[m.games_old != m.games_new].nlargest(8, "dvorp",
                                                    keep="all").head(8)
    names = pd.read_sql_query(
        "SELECT player_id, player_name FROM dim_players", con)
    movers = movers.merge(names, on="player_id", how="left")
    print(f"\n{(m.games_old != m.games_new).sum()} players' game counts "
          f"changed (POST weeks removed). Largest VORP gainers (playoff "
          f"weeks were DRAGGING their ppg):")
    print(movers[["player_name", "games_old", "games_new", "ppg_old",
                  "ppg_new", "vorp_old", "vorp_new"]].to_string(index=False))
    print(f"\nmedian |Δvorp| among changed: "
          f"{m.loc[m.games_old != m.games_new, 'dvorp'].abs().median():.2f}; "
          f"max |Δvorp|: {m.dvorp.abs().max():.2f}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
