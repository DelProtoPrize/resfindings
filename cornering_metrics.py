"""
cornering_metrics.py — Step 4: market cornering on a FIXED replacement bar.

THE REPLACEMENT-LEVEL DECISION (governs everything here):
The bar is FIXED REALIZED replacement — player_production_value.replacement_ppg
per (league, position), from the most recent completed season — held constant
for BOTH bases. Player production in the numerator may be realized or
projected; the denominator bar does not move.

Why fixed (the defensible reasoning, verbatim): holding replacement fixed
localizes all forecast error to the numerator, so every point of share or
cornering movement traces to a specific roster's players rather than to the
pool re-forecasting. Projection-derived replacement would put prediction
error in the bar and the player simultaneously — and our own m1 compresses
the QB tail, which would shift cross-position balance for a mechanical
reason, not a roster-skill reason. Fixed bar = clean attribution.

KNOWN CAVEAT (also ships in the UI tooltip — a panelist should never find it
first): a fixed realized bar can go stale if positional scarcity shifts
(rookie QB class, scoring-rule change). Over a single offseason the drift is
small and attribution clarity is worth more; revisit if the metric is ever
extended multi-year.

CURRENCY HANDLING (the one place care is needed): realized PPG is in each
league's own scoring; ppg_proj is in canonical (Drew) currency. A bar must
share its numerator's currency, so:
  realized basis : bar = replacement_ppg as stored (league currency).
  projected basis: the SAME replacement rank K (recovered per league/position
    as the bar's rank within player_production_value's pool), re-expressed in
    canonical currency as the K-th ranked canonical realized PPG. Same player
    cutoff, same season, same pool — only the unit changes. For the canonical
    league the two bars are identical (verified at runtime).

METRICS per (league, position):
  VONA            max(production - fixed_bar, 0) per player (positional
                  value-over-next-available; pooled-across-positions would
                  erase the cornering signal entirely).
  vona_share      team's summed VONA / league-wide summed VONA.
  elite held      count of a team's players with VONA > 0 ("RB1s held:
                  X holds 4 of 12").
  positional HHI  sum of squared team shares — how concentrated leaguewide
                  above-replacement production is at that position.

Tables (idempotent per basis + as-of):
  positional_cornering(basis, as_of_date, league_id, position, roster_id,
      vona, vona_share, elite_count)
  positional_cornering_league(basis, as_of_date, league_id, position,
      replacement_bar, bar_currency, hhi, elite_total,
      top_roster_id, top_share, n_unprojected)

Run:  python cornering_metrics.py --db data/dynasty.db
      (projected basis requires project_production.py to have run; if its
       table is absent, the realized basis still builds and the projected
       basis is skipped with a notice — dashes downstream, never fabrication)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date

DDL = """
CREATE TABLE IF NOT EXISTS positional_cornering (
    basis TEXT, as_of_date TEXT, league_id TEXT, position TEXT,
    roster_id INTEGER, vona REAL, vona_share REAL, elite_count INTEGER,
    PRIMARY KEY (basis, as_of_date, league_id, position, roster_id)
);
CREATE TABLE IF NOT EXISTS positional_cornering_league (
    basis TEXT, as_of_date TEXT, league_id TEXT, position TEXT,
    replacement_bar REAL, bar_currency TEXT, hhi REAL, elite_total INTEGER,
    top_roster_id INTEGER, top_share REAL, n_unprojected INTEGER,
    PRIMARY KEY (basis, as_of_date, league_id, position)
);
"""
POSITIONS = ("QB", "RB", "WR", "TE")


def fixed_bars(con) -> dict[tuple[str, str], tuple[float, int]]:
    """(league, pos) -> (realized replacement_ppg, implied rank K within
    player_production_value's own pool). Single source of truth."""
    out = {}
    for lid, pos, bar in con.execute(
            "SELECT DISTINCT league_id, position, replacement_ppg "
            "FROM player_production_value WHERE position IN (?,?,?,?)",
            POSITIONS):
        k = con.execute(
            "SELECT COUNT(*) + 1 FROM player_production_value "
            "WHERE league_id=? AND position=? AND ppg > ?",
            (lid, pos, bar)).fetchone()[0]
        out[(lid, pos)] = (bar, k)
    return out


def canonical_bars(con, bars, canonical) -> dict[tuple[str, str], float]:
    """Same rank K, re-expressed in canonical currency: the K-th ranked
    canonical realized PPG. For the canonical league this must reproduce the
    stored bar (asserted by the caller's verification)."""
    out = {}
    for (lid, pos), (_bar, k) in bars.items():
        row = con.execute(
            "SELECT ppg FROM player_production_value "
            "WHERE league_id=? AND position=? ORDER BY ppg DESC "
            "LIMIT 1 OFFSET ?", (canonical, pos, k - 1)).fetchone()
        if row:
            out[(lid, pos)] = row[0]
    return out


def write_basis(con, basis, as_of, rows_by_league_pos):
    for (lid, pos), data in rows_by_league_pos.items():
        bar, currency, players, n_unproj = data
        per_roster: dict[int, list] = {}
        for rid, prod in players:
            v = max((prod or 0) - bar, 0.0)
            e = per_roster.setdefault(rid, [0.0, 0])
            e[0] += v
            e[1] += 1 if v > 0 else 0
        total = sum(v for v, _ in per_roster.values())
        hhi, top_rid, top_share = 0.0, None, 0.0
        for rid, (v, elite) in sorted(per_roster.items()):
            share = (v / total) if total > 0 else None
            if share is not None:
                hhi += share * share
                if share > top_share:
                    top_rid, top_share = rid, share
            con.execute(
                "INSERT OR REPLACE INTO positional_cornering VALUES "
                "(?,?,?,?,?,?,?,?)",
                (basis, as_of, lid, pos, rid, round(v, 2),
                 None if share is None else round(share, 6), elite))
        con.execute(
            "INSERT OR REPLACE INTO positional_cornering_league VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?)",
            (basis, as_of, lid, pos, round(bar, 4), currency,
             round(hhi, 4) if total > 0 else None,
             sum(e for _, e in per_roster.values()),
             top_rid, round(top_share, 6) if total > 0 else None, n_unproj))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/dynasty.db")
    ap.add_argument("--as-of", default=str(date.today()))
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    con.executescript(DDL)
    canonical = con.execute("SELECT league_id FROM outcomes_provenance "
                            "WHERE is_canonical=1").fetchone()[0]

    bars = fixed_bars(con)
    cbars = canonical_bars(con, bars, canonical)
    season = con.execute(
        "SELECT MAX(season) FROM player_production_value").fetchone()[0]
    print(f"fixed realized bar — season {season}, from "
          f"player_production_value.replacement_ppg:")
    for (lid, pos), (bar, k) in sorted(bars.items()):
        if lid == canonical:
            print(f"  canonical {pos}: bar {bar} ppg (implied rank K={k}; "
                  f"canonical-currency re-expression {cbars[(lid, pos)]})")

    # current rostered world, scoped to latest season-with-data per league
    league_rows = con.execute(
        "SELECT l.league_id FROM dim_leagues l WHERE l.season = "
        "(SELECT MAX(d2.season) FROM dim_leagues d2 JOIN v_player_value v "
        " ON v.league_id=d2.league_id WHERE d2.league_name=l.league_name)"
    ).fetchall()
    leagues = [r[0] for r in league_rows]

    # ---- realized basis ------------------------------------------------------
    work = {}
    for lid in leagues:
        for pos in POSITIONS:
            if (lid, pos) not in bars:
                continue
            players = con.execute(
                "SELECT roster_id, ppg FROM v_player_value "
                "WHERE league_id=? AND position=? AND ppg IS NOT NULL AND "
                "snapshot_date=(SELECT MAX(snapshot_date) FROM v_player_value "
                "WHERE league_id=?)", (lid, pos, lid)).fetchall()
            work[(lid, pos)] = (bars[(lid, pos)][0], "league", players, 0)
    con.execute("DELETE FROM positional_cornering WHERE basis='realized' AND as_of_date=?", (args.as_of,))
    con.execute("DELETE FROM positional_cornering_league WHERE basis='realized' AND as_of_date=?", (args.as_of,))
    write_basis(con, "realized", args.as_of, work)
    print(f"realized basis: {len(work)} (league, position) cells written")

    # ---- projected basis (skipped honestly if Step 3 hasn't run) -------------
    has_proj = con.execute(
        "SELECT name FROM sqlite_master WHERE name='player_projected_value'"
    ).fetchone()
    if has_proj:
        work = {}
        for lid in leagues:
            asof_proj = con.execute(
                "SELECT MAX(as_of_date) FROM player_projected_value "
                "WHERE league_id=?", (lid,)).fetchone()[0]
            if asof_proj is None:
                continue
            for pos in POSITIONS:
                if (lid, pos) not in cbars:
                    continue
                players = con.execute(
                    "SELECT roster_id, ppg_proj FROM player_projected_value "
                    "WHERE league_id=? AND position=? AND as_of_date=? "
                    "AND ppg_proj IS NOT NULL", (lid, pos, asof_proj)).fetchall()
                n_unproj = con.execute(
                    "SELECT COUNT(*) FROM player_projected_value "
                    "WHERE league_id=? AND position=? AND as_of_date=? "
                    "AND ppg_proj IS NULL", (lid, pos, asof_proj)).fetchone()[0]
                work[(lid, pos)] = (cbars[(lid, pos)], "canonical",
                                    players, n_unproj)
        con.execute("DELETE FROM positional_cornering WHERE basis='projected' AND as_of_date=?", (args.as_of,))
        con.execute("DELETE FROM positional_cornering_league WHERE basis='projected' AND as_of_date=?", (args.as_of,))
        write_basis(con, "projected", args.as_of, work)
        print(f"projected basis: {len(work)} cells written "
              f"(numerator ppg_proj, canonical-currency bar, same K)")
        # Deferred Step 3 item, landed here because this module owns the bar:
        # a v_player_value-shaped view over projections, so lineup_solver.py
        # can run on the projected world (--source v_player_value_projected).
        # vorp is VONA against the SAME fixed bar (never m1's moving
        # replacement), keeping the solver's surplus filter consistent with
        # every other Step 4 number.
        con.executescript("""
            DROP VIEW IF EXISTS v_player_value_projected;
            CREATE VIEW v_player_value_projected AS
            SELECT p.as_of_date  AS snapshot_date,
                   p.league_id, p.roster_id, p.player_id, p.player_name,
                   p.position,
                   p.ppg_proj    AS ppg,
                   CASE WHEN p.ppg_proj > b.replacement_bar
                        THEN ROUND(p.ppg_proj - b.replacement_bar, 2)
                        ELSE 0 END AS vorp
            FROM player_projected_value p
            JOIN positional_cornering_league b
              ON b.league_id = p.league_id AND b.position = p.position
             AND b.basis = 'projected' AND b.as_of_date = p.as_of_date;
        """)
        print("view v_player_value_projected (fixed-bar vorp) refreshed")
    else:
        print("projected basis SKIPPED — player_projected_value absent "
              "(run project_production.py); downstream shows dashes")
    con.commit()

    # ---- verification gates --------------------------------------------------
    # tolerance 1e-5: shares are STORED rounded to 6dp, so a 14-roster league
    # can legitimately sum to 1.0 ± 7e-6; the unrounded shares sum to 1 by
    # construction (vona / total).
    print("\nverification — positional shares sum to 1.0 per (league, position):")
    bad = con.execute("""
        SELECT basis, league_id, position, ROUND(SUM(vona_share), 6) s
        FROM positional_cornering WHERE as_of_date=? AND vona_share IS NOT NULL
        GROUP BY basis, league_id, position
        HAVING ABS(s - 1.0) > 1e-5""", (args.as_of,)).fetchall()
    print("  ALL OK" if not bad else f"  FAILURES: {bad}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
