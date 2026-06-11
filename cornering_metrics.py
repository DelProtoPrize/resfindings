"""
lineup_solver.py — Step 2: slot-eligibility matrix + Hungarian optimal lineup.

WHY HUNGARIAN, NOT GREEDY (the deliberate talking point):
With partial-overlap flex slots, greedy "fill most-restrictive slot with the
best remaining eligible player" is NOT optimal. Concrete counterexample,
embedded as a self-test below (slots WR / WRRB_FLEX / REC_FLEX — both exist
in our real leagues):
    WR_A 10pts, WR_B 9, RB_C 8, TE_D 2
    greedy:  WR<-WR_A(10), WRRB_FLEX<-WR_B(9), REC_FLEX<-TE_D(2)  = 21
    optimal: WR<-WR_A(10), WRRB_FLEX<-RB_C(8), REC_FLEX<-WR_B(9)  = 27
Greedy parks WR_B in the slot RB_C needed; the assignment problem sees the
whole matrix. scipy.optimize.linear_sum_assignment solves it exactly.

SCORING RULE: the lineup is scored on POINTS, never VORP. A lineup scores
points; the SUPER_FLEX "start an RB over QB2 if it outprojects" call is a
points comparison. VORP enters only afterward, as the surplus-quality filter:
  true surplus = players who LOSE the assignment AND have VORP>0
  (startable trade capital — the number that makes RB-cornering concrete).
Picks are never in the lineup; they are capital of a separate kind (Step 1).

POINTS INPUT (until Step 3): REG-only realized PPG from v_player_value,
labeled as such in roster_construction.points_basis. The solver takes the
points column as a parameter; Step 3's projections plug into the same
interface without touching the assignment code.

Slots: derived per league from dim_leagues.roster_positions_json.
  QB{QB} RB{RB} WR{WR} TE{TE} FLEX{RB,WR,TE}
  SUPER_FLEX{QB,RB,WR,TE} WRRB_FLEX{WR,RB} REC_FLEX{WR,TE}
  Unsupported (K/DEF/IDP_*) are skipped and counted — no valuations exist
  for those positions, so pretending to fill them would be fabrication.

Tables written (idempotent per league+snapshot):
  roster_lineup_optimal(snapshot_date, league_id, roster_id, slot, slot_seq,
                        player_id, player_name, position, points)
  roster_surplus(snapshot_date, league_id, roster_id, player_id, player_name,
                 position, points, vorp)
  roster_construction(snapshot_date, league_id, roster_id, osl_points,
                      slots_filled, slots_empty, skipped_slots,
                      surplus_count, surplus_vorp, surplus_points,
                      greedy_points, hungarian_gain, points_basis)

Run:  python lineup_solver.py --db data/dynasty.db
Requires scipy — add `scipy` to etl/requirements.txt.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys

import numpy as np
from scipy.optimize import linear_sum_assignment

ELIGIBILITY = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"},
    "FLEX": {"RB", "WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "WRRB_FLEX": {"WR", "RB"},
    "REC_FLEX": {"WR", "TE"},
}
NON_LINEUP = {"BN", "IR", "TAXI"}

DDL = """
CREATE TABLE IF NOT EXISTS roster_lineup_optimal (
    snapshot_date TEXT, league_id TEXT, roster_id INTEGER,
    slot TEXT, slot_seq INTEGER, player_id TEXT, player_name TEXT,
    position TEXT, points REAL,
    PRIMARY KEY (snapshot_date, league_id, roster_id, slot, slot_seq)
);
CREATE TABLE IF NOT EXISTS roster_surplus (
    snapshot_date TEXT, league_id TEXT, roster_id INTEGER,
    player_id TEXT, player_name TEXT, position TEXT, points REAL, vorp REAL,
    PRIMARY KEY (snapshot_date, league_id, roster_id, player_id)
);
CREATE TABLE IF NOT EXISTS roster_construction (
    snapshot_date TEXT, league_id TEXT, roster_id INTEGER,
    osl_points REAL, slots_filled INTEGER, slots_empty INTEGER,
    skipped_slots TEXT, surplus_count INTEGER, surplus_vorp REAL,
    surplus_points REAL, greedy_points REAL, hungarian_gain REAL,
    points_basis TEXT,
    PRIMARY KEY (snapshot_date, league_id, roster_id)
);
"""


# --------------------------------------------------------------------------- #
# solvers
# --------------------------------------------------------------------------- #

def solve_hungarian(slots: list[str], players: list[dict]) -> tuple[list, float, int]:
    """Optimal assignment. Returns (assignment rows, total points, empty slots).
    Forbidden pairs are np.inf cost; dummy zero-point players pad feasibility
    when a roster has fewer eligible bodies than slots (those slots report
    empty rather than crashing the matching)."""
    n_s, n_p = len(slots), len(players)
    pad = max(0, n_s - n_p) + n_s  # always enough dummies for feasibility
    cost = np.full((n_s, n_p + pad), np.inf)
    for i, slot in enumerate(slots):
        elig = ELIGIBILITY[slot]
        for j, p in enumerate(players):
            if p["position"] in elig:
                cost[i, j] = -(p["points"] or 0.0)
        cost[i, n_p:] = 0.0  # dummies: eligible everywhere, worth 0
    rows, cols = linear_sum_assignment(cost)
    out, total, empty = [], 0.0, 0
    for i, j in zip(rows, cols):
        if j >= n_p:
            empty += 1
            continue
        p = players[j]
        out.append((slots[i], p))
        total += p["points"] or 0.0
    return out, total, empty


def solve_greedy(slots: list[str], players: list[dict]) -> float:
    """The benchmark to beat: most-restrictive slot first, best remaining
    eligible player. Kept ONLY to measure the Hungarian gain — never used
    for real output."""
    order = sorted(range(len(slots)), key=lambda i: len(ELIGIBILITY[slots[i]]))
    taken, total = set(), 0.0
    for i in order:
        elig = ELIGIBILITY[slots[i]]
        best, bj = -1.0, None
        for j, p in enumerate(players):
            if j in taken or p["position"] not in elig:
                continue
            if (p["points"] or 0.0) > best:
                best, bj = (p["points"] or 0.0), j
        if bj is not None:
            taken.add(bj)
            total += best
    return total


def self_test() -> None:
    """The counterexample from the module docstring, asserted."""
    slots = ["WR", "WRRB_FLEX", "REC_FLEX"]
    players = [
        {"position": "WR", "points": 10.0, "player_id": "A", "player_name": "WR_A", "vorp": 0},
        {"position": "WR", "points": 9.0,  "player_id": "B", "player_name": "WR_B", "vorp": 0},
        {"position": "RB", "points": 8.0,  "player_id": "C", "player_name": "RB_C", "vorp": 0},
        {"position": "TE", "points": 2.0,  "player_id": "D", "player_name": "TE_D", "vorp": 0},
    ]
    g = solve_greedy(slots, players)
    _, h, _ = solve_hungarian(slots, players)
    assert g == 21.0 and h == 27.0, f"counterexample broken: greedy={g}, hungarian={h}"


# --------------------------------------------------------------------------- #
# pipeline
# --------------------------------------------------------------------------- #

def league_slots(rp_json: str) -> tuple[list[str], list[str]]:
    slots, skipped = [], []
    for s in json.loads(rp_json):
        if s in NON_LINEUP:
            continue
        (slots if s in ELIGIBILITY else skipped).append(s)
    return slots, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/dynasty.db")
    ap.add_argument("--points-col", default="ppg",
                    help="points column of the source view used as lineup points")
    ap.add_argument("--source", default="v_player_value",
                    choices=["v_player_value", "v_player_value_projected"],
                    help="v_player_value = realized REG-only; "
                         "v_player_value_projected = m1 projection with "
                         "fixed-bar vorp (built by cornering_metrics.py)")
    args = ap.parse_args()
    self_test()
    con = sqlite3.connect(args.db)
    con.executescript(DDL)
    if args.source == "v_player_value_projected":
        basis = (f"{args.source}.{args.points_col} (m1 projection, canonical "
                 f"currency; preseason skill ≈ ECR baseline — see Model Lab; "
                 f"vorp = VONA vs fixed realized bar)")
    elif args.points_col == "ppg":
        basis = f"{args.source}.ppg (REG-only realized)"
    else:
        basis = f"{args.source}.{args.points_col}"

    # Latest season PER LEAGUE THAT HAS VALUE DATA — a rolled-over season row
    # with no synced rosters (e.g. a pre-draft new year) must not silently
    # shadow the season that actually has a warehouse snapshot.
    leagues = con.execute(
        "SELECT l.league_id, l.league_name, l.roster_positions_json "
        "FROM dim_leagues l "
        f"WHERE l.season = (SELECT MAX(d2.season) FROM dim_leagues d2 "
        f"  JOIN {args.source} v ON v.league_id = d2.league_id "
        f"  WHERE d2.league_name = l.league_name)").fetchall()

    grand_gain = 0.0
    for lid, lname, rp in leagues:
        slots, skipped = league_slots(rp)
        snap = con.execute(
            f"SELECT MAX(snapshot_date) FROM {args.source} WHERE league_id=?",
            (lid,)).fetchone()[0]
        if snap is None:
            print(f"{lname}: SKIPPED — no v_player_value snapshot for {lid}")
            continue
        con.execute("DELETE FROM roster_lineup_optimal WHERE league_id=? AND snapshot_date=?", (lid, snap))
        con.execute("DELETE FROM roster_surplus WHERE league_id=? AND snapshot_date=?", (lid, snap))
        con.execute("DELETE FROM roster_construction WHERE league_id=? AND snapshot_date=?", (lid, snap))

        rosters = [r[0] for r in con.execute(
            f"SELECT DISTINCT roster_id FROM {args.source} "
            f"WHERE league_id=? AND snapshot_date=?", (lid, snap))]
        div = 0
        for rid in rosters:
            players = [dict(zip(("player_id", "player_name", "position",
                                 "points", "vorp"), row))
                       for row in con.execute(
                f"SELECT player_id, player_name, position, {args.points_col}, vorp "
                f"FROM {args.source} WHERE league_id=? AND roster_id=? "
                f"AND snapshot_date=? AND position IN ('QB','RB','WR','TE')",
                (lid, rid, snap))]
            lineup, osl, empty = solve_hungarian(slots, players)
            greedy = solve_greedy(slots, players)
            gain = osl - greedy
            if gain > 1e-9:
                div += 1
            grand_gain += max(gain, 0)

            seq: dict[str, int] = {}
            starters = set()
            for slot, p in lineup:
                seq[slot] = seq.get(slot, 0) + 1
                starters.add(p["player_id"])
                con.execute(
                    "INSERT INTO roster_lineup_optimal VALUES (?,?,?,?,?,?,?,?,?)",
                    (snap, lid, rid, slot, seq[slot], p["player_id"],
                     p["player_name"], p["position"], p["points"]))
            surplus = [p for p in players
                       if p["player_id"] not in starters and (p["vorp"] or 0) > 0]
            for p in surplus:
                con.execute("INSERT INTO roster_surplus VALUES (?,?,?,?,?,?,?,?)",
                            (snap, lid, rid, p["player_id"], p["player_name"],
                             p["position"], p["points"], p["vorp"]))
            con.execute(
                "INSERT INTO roster_construction VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (snap, lid, rid, round(osl, 2), len(lineup), empty,
                 ",".join(sorted(set(skipped))) or None, len(surplus),
                 round(sum(p["vorp"] or 0 for p in surplus), 2),
                 round(sum(p["points"] or 0 for p in surplus), 2),
                 round(greedy, 2), round(gain, 4), basis))
        con.commit()
        print(f"{lname}: {len(rosters)} rosters solved "
              f"({len(slots)} lineup slots; skipped: {sorted(set(skipped)) or 'none'}); "
              f"greedy diverged on {div} rosters")
    print(f"total Hungarian gain over greedy across all rosters: "
          f"{grand_gain:.2f} pts/wk")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
