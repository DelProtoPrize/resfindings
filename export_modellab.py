"""
export_modellab.py — flatten harness results into web/data/modellab.json.

The Model Lab page fetches one static JSON, so it works under any static
server (or your Express app's static middleware) with zero route changes.
Re-run this after any backtest run to refresh the page. If you later prefer
a live endpoint, the JSON shape below IS the API contract — serve the same
object from GET /api/modellab.

Usage:  python export_modellab.py --db data/dynasty.db --out ../web/data/modellab.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

POS_ORDER = ["QB", "RB", "WR", "TE"]


def get(con, model, season, pos, metric):
    r = con.execute(
        "SELECT value, n FROM evaluations WHERE model_id=? AND test_season=? "
        "AND position=? AND metric=?", (model, season, pos, metric)).fetchone()
    return (r[0], r[1]) if r else (None, None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/dynasty.db")
    ap.add_argument("--out", default="../web/data/modellab.json")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)

    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "evaluations" not in have:
        sys.exit("No evaluations table — run backtest_baselines.py first.")
    pooled_ok = con.execute(
        "SELECT COUNT(*) FROM evaluations WHERE test_season=0").fetchone()[0]
    if not pooled_ok:
        sys.exit("evaluations has no pooled rows (test_season=0) — re-run "
                 "backtest_baselines.py and projection_model.py (latest "
                 "versions persist their headline metrics).")

    out: dict = {"generated_from": str(Path(args.db).resolve())}

    # ---- verdicts (the ledger) -----------------------------------------------
    b1, n_b1 = get(con, "b1_ecr_v1", 0, "ALL", "skill_vs_b0")
    b1_lo, _ = get(con, "b1_ecr_v1", 0, "ALL", "skill_vs_b0_ci_lo")
    b1_hi, _ = get(con, "b1_ecr_v1", 0, "ALL", "skill_vs_b0_ci_hi")
    m1, n_m1 = get(con, "m1_ridge_v1", 0, "ALL", "skill_vs_b1")
    m1_lo, _ = get(con, "m1_ridge_v1", 0, "ALL", "skill_vs_b1_ci_lo")
    m1_hi, _ = get(con, "m1_ridge_v1", 0, "ALL", "skill_vs_b1_ci_hi")
    ins, n_ins = get(con, "m1_ridge_v1", 0, "ALL", "skill_vs_b1_inseason")
    ins_lo, _ = get(con, "m1_ridge_v1", 0, "ALL", "skill_vs_b1_inseason_ci_lo")
    ins_hi, _ = get(con, "m1_ridge_v1", 0, "ALL", "skill_vs_b1_inseason_ci_hi")
    out["verdicts"] = [
        {"q": "Does expert rank beat naive persistence?",
         "detail": "B1 (flat ECR curve) vs B0 (last-season ppg carried "
                   "forward), rest-of-season points, common support",
         "skill": b1, "lo": b1_lo, "hi": b1_hi, "n": n_b1},
        {"q": "Does production data add anything ECR doesn't know?",
         "detail": "m1 (ridge: B1 prediction + production features) vs B1, "
                   "all as-of dates pooled",
         "skill": m1, "lo": m1_lo, "hi": m1_hi, "n": n_m1},
        {"q": "…what about once the season is underway?",
         "detail": "same comparison, week 5/9/13 as-of dates only "
                   "(pre-registered split)",
         "skill": ins, "lo": ins_lo, "hi": ins_hi, "n": n_ins},
    ]

    # ---- per-position skills --------------------------------------------------
    out["by_position"] = []
    for pos in POS_ORDER:
        s_b1, n1 = get(con, "b1_ecr_v1", 0, pos, "skill_vs_b0")
        s_m1, n2 = get(con, "m1_ridge_v1", 0, pos, "skill_vs_b1")
        out["by_position"].append(
            {"pos": pos, "b1_vs_b0": s_b1, "m1_vs_b1": s_m1,
             "n": n1 or n2})

    # ---- in-season accrual ----------------------------------------------------
    wk = pd.read_sql_query(
        "SELECT metric, value, n FROM evaluations WHERE model_id='m1_ridge_v1' "
        "AND test_season=0 AND position='ALL' AND metric LIKE "
        "'skill_vs_b1_wk%'", con)
    out["by_week"] = sorted(
        [{"week": int(m.replace("skill_vs_b1_wk", "")), "skill": v, "n": n}
         for m, v, n in wk.itertuples(index=False)], key=lambda d: d["week"])

    # ---- per-season trend -----------------------------------------------------
    tr = pd.read_sql_query(
        "SELECT model_id, test_season, metric, value FROM evaluations "
        "WHERE test_season>0 AND metric IN ('skill_vs_b0','skill_vs_b1')", con)
    seasons = sorted(tr.test_season.unique().tolist())
    def season_series(model, metric):
        # n-weighted across positions per season
        q = pd.read_sql_query(
            "SELECT test_season, value, n FROM evaluations WHERE model_id=? "
            "AND metric=? AND test_season>0", con, params=(model, metric))
        g = (q.assign(w=q.value * q.n).groupby("test_season")
              .apply(lambda x: x.w.sum() / x.n.sum(), include_groups=False))
        return [round(float(g.get(s)), 4) if s in g.index else None
                for s in seasons]
    out["by_season"] = {
        "seasons": seasons,
        "b1_vs_b0": season_series("b1_ecr_v1", "skill_vs_b0"),
        "m1_vs_b1": season_series("m1_ridge_v1", "skill_vs_b1"),
    }

    # ---- coefficients ---------------------------------------------------------
    if "model_coefficients" in have:
        cf = pd.read_sql_query(
            "SELECT position, feature, AVG(beta) AS beta FROM "
            "model_coefficients WHERE model_id='m1_ridge_v1' "
            "GROUP BY position, feature", con)
        feats = [f for f in cf.feature.unique().tolist()]
        # order: b1 first, then by mean |beta|
        order = (cf.groupby("feature").beta.apply(lambda s: s.abs().mean())
                 .sort_values(ascending=False).index.tolist())
        order = ["b1_rate"] + [f for f in order if f != "b1_rate"]
        piv = cf.pivot(index="feature", columns="position", values="beta") \
                .reindex(order).reindex(columns=POS_ORDER)
        out["coefficients"] = {
            "features": piv.index.tolist(),
            "positions": POS_ORDER,
            "beta": [[None if pd.isna(v) else round(float(v), 3)
                      for v in row] for row in piv.to_numpy()],
        }
    else:
        out["coefficients"] = None

    # ---- protocol footnote data ----------------------------------------------
    runs = pd.read_sql_query(
        "SELECT model_id, MAX(run_ts) AS last_run FROM model_runs "
        "GROUP BY model_id", con)
    npred = con.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    out["protocol"] = {
        "runs": runs.to_dict("records"),
        "n_predictions": int(npred),
        "grid": "as-of = day before weeks 1/5/9/13, seasons 2020-2025, "
                "expanding-window training from 2019",
        "currency": "The Drew League points (14-team SF, TE-premium 0.5), "
                    "REG season only",
    }

    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1))
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
