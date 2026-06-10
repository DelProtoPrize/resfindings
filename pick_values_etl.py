"""
pick_values_etl.py — Step 1: fold draft picks into Value Share.

WHY: teams holding future picks were unfairly penalized in Value Share —
v_roster_assets already UNION-ALLs picks into the asset stream, but
dim_draft_picks.pick_value_{1qb,2qb} were NULL, so picks flowed as nothing.
Populating them lights up Value Share with ZERO server or client changes.

SOURCE: FantasyCalc values/current — settings-aware (the brief's preference),
and it serves picks natively with position='PICK', including the round-level
generics our schema needs ('2026 1st', '2027 2nd', ...). Our picks carry
(year, round) but no slot, so they map to the round generic, which is FC's
own market average across slots — not a fudge.

SETTINGS: the schema stores exactly two value columns (1qb / 2qb) and
v_roster_assets selects by dim_leagues.is_superflex. So:
  pick_value_2qb <- FC numQbs=2, numTeams=14 (the canonical Drew settings)
  pick_value_1qb <- FC numQbs=1, numTeams=12
Documented limitation: non-canonical league sizes inherit these two curves.

HONESTY RULES:
  - A pick's value is speculative until the rookie exists. The UI-facing flag
    is the EXISTING v_roster_assets.asset_type='PICK' (position='PICK') —
    nothing new needed; downstream code keys off it.
  - Picks NEVER enter production metrics: the /production route reads
    v_player_value (players only), so Production Share excludes picks by
    construction. The Value-Share-up / Production-Share-flat divergence for
    rebuilders is correct and is itself the signal.
  - Past-year picks (already drafted; attached to historical league-season
    rows) have no current market price and stay NULL — reported, not hidden.

Idempotent: re-runs refresh values in place (stamped with valued_at).

Run:  python pick_values_etl.py --db data/dynasty.db
New columns: dim_draft_picks.valued_at (added if absent). No new tables.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import date

import requests

FC_URL = "https://api.fantasycalc.com/values/current"
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}
HEADERS = {"User-Agent": "dynasty-portfolio pick ETL"}


def fetch_fc_pick_curve(num_qbs: int, num_teams: int) -> dict[tuple[str, int], int]:
    """(year, round) -> value, from FC's round-level generic pick entries."""
    r = requests.get(FC_URL, headers=HEADERS, timeout=30, params={
        "isDynasty": "true", "numQbs": num_qbs, "numTeams": num_teams, "ppr": 1})
    r.raise_for_status()
    curve: dict[tuple[str, int], int] = {}
    for d in r.json():
        p = d.get("player", {})
        if p.get("position") != "PICK":
            continue
        m = re.fullmatch(r"(20\d\d) (\d)(?:st|nd|rd|th)", p.get("name", ""))
        if m:  # round generic like '2027 2nd' (slot picks like '2026 Pick 1.01' skipped)
            curve[(m.group(1), int(m.group(2)))] = int(d["value"])
    return curve


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/dynasty.db")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)

    cols = {r[1] for r in con.execute("PRAGMA table_info(dim_draft_picks)")}
    if "valued_at" not in cols:
        con.execute("ALTER TABLE dim_draft_picks ADD COLUMN valued_at TEXT")

    c2 = fetch_fc_pick_curve(num_qbs=2, num_teams=14)
    c1 = fetch_fc_pick_curve(num_qbs=1, num_teams=12)
    print(f"FC curve coverage: 2QB {len(c2)} (year,round) pairs, 1QB {len(c1)}")
    yr_now = str(date.today().year)

    picks = con.execute(
        "SELECT pick_id, year, round FROM dim_draft_picks").fetchall()
    updated, future_unpriced, past = 0, [], 0
    for pid, year, rnd in picks:
        if str(year) < yr_now:
            past += 1            # already drafted; no current market price
            continue
        v2, v1 = c2.get((str(year), rnd)), c1.get((str(year), rnd))
        if v2 is None and v1 is None:
            future_unpriced.append((year, rnd))
            continue
        con.execute(
            "UPDATE dim_draft_picks SET pick_value_2qb=?, pick_value_1qb=?, "
            "pick_value_tier=?, valued_at=date('now') WHERE pick_id=?",
            (v2, v1, f"{year} {ORDINAL.get(rnd, str(rnd) + 'th')}", pid))
        updated += 1
    con.commit()
    print(f"picks valued: {updated} | past-year (NULL by design): {past} | "
          f"future but unpriced by FC: {len(future_unpriced)} "
          f"{sorted(set(future_unpriced)) if future_unpriced else ''}")

    # ---- verification: shares sum to 1.0 with picks; divergence direction ----
    for (lid, name) in con.execute(
            "SELECT league_id, league_name FROM dim_leagues "
            "WHERE season = (SELECT MAX(season) FROM dim_leagues d2 "
            "WHERE d2.league_name = dim_leagues.league_name)"):
        rows = con.execute("""
            SELECT roster_id,
                   SUM(fp_market_value)                                   AS total_v,
                   SUM(CASE WHEN asset_type='PICK' THEN fp_market_value
                            ELSE 0 END)                                   AS pick_v
            FROM v_roster_assets
            WHERE league_id=? AND snapshot_date=
                  (SELECT MAX(snapshot_date) FROM v_roster_assets WHERE league_id=?)
            GROUP BY roster_id""", (lid, lid)).fetchall()
        tot = sum(r[1] or 0 for r in rows)
        if not tot:
            continue
        share_sum = sum((r[1] or 0) / tot for r in rows)
        pick_pct = sum(r[2] or 0 for r in rows) / tot
        print(f"{name}: shares sum {share_sum:.6f} | picks now "
              f"{pick_pct:.1%} of league value across {len(rows)} rosters")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
