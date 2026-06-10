"""
projection_model.py — build-order #4: the model the harness was built to grade.

THE QUESTION: does production data add anything FantasyPros ECR doesn't
already know? The design makes the answer a single number:
  m1 'm1_ridge_v1' = per-position ridge regression on
      [ B1's own curve prediction ]  +  production features
      (trailing ppg windows, availability, volatility, boom/bust, age,
       seasons_in_league, has_history)
  Because B1's prediction is INSIDE the feature set, skill_m1_vs_B1 > 0 is
  exactly the marginal value of production data over expert rank. If the
  ridge zeroes everything but the B1 feature, the honest answer is "no".

Modeling choices (defensibility over complexity):
  - Closed-form numpy ridge: zero new dependencies, every standardized
    coefficient inspectable (printed per position).
  - Per-position fits: QB/RB/WR/TE have different aging and usage dynamics;
    pooling would launder them through one slope.
  - Missing production (rookies, returners): position-median imputation
    using TRAIN data only, plus a has_history indicator — the model learns
    how much to trust a player with no track record.
  - Lambda by inner validation: last train season held out, grid
    {1,10,100,1000}, refit on full train with the winner.
  - Target = per-remaining-week scoring rate (as B1), so different as_ofs
    pool coherently; total = rate * weeks_remaining.

Protocol identical to backtest_baselines (same grid, same chokepoint, same
append-only logging), so numbers are directly comparable.

Usage:  python projection_model.py --db data/dynasty.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

import numpy as np
import pandas as pd

import build_features as _bf
from build_features import build_features  # noqa: F401  (schema handshake)
from backtest_baselines import (DDL, GRID_WEEKS, TEST_SEASONS, as_of_grid,
                                b1_predict, features_with_rank, log_predictions,
                                realized, train_b1_curve)

if getattr(_bf, "SCHEMA_VERSION", 1) < 2:
    sys.exit("Stale build_features.py (pre-v2) — replace it with the latest.")

MODEL_ID = "m1_ridge_v1"
LAMBDAS = (1.0, 10.0, 100.0, 1000.0)
PROD_FEATURES = ["ppg_w4", "ppg_w8", "ppg_w17", "sd_pts_w8", "cv_pts_w8",
                 "boom_rate_w8", "bust_rate_w8", "availability_w17",
                 "ppg_prev_season", "age_asof", "seasons_in_league"]


# ---------------------------------------------------------------------------
# ridge plumbing (numpy, standardized X, unpenalized intercept)
# ---------------------------------------------------------------------------

class Ridge:
    def fit(self, X: np.ndarray, y: np.ndarray, lam: float) -> "Ridge":
        self.mu, self.sd = X.mean(0), X.std(0)
        self.sd[self.sd == 0] = 1.0
        Z = (X - self.mu) / self.sd
        self.ybar = y.mean()
        A = Z.T @ Z + lam * np.eye(Z.shape[1])
        self.beta = np.linalg.solve(A, Z.T @ (y - self.ybar))
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return ((X - self.mu) / self.sd) @ self.beta + self.ybar


def assemble_xy(frames: list[pd.DataFrame], medians: pd.DataFrame | None
                ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Stack (features, target-rate) pairs; impute with TRAIN medians only.
    Returns (df with X columns + meta, medians used)."""
    df = pd.concat(frames, ignore_index=True)
    df["has_history"] = df.ppg_prev_season.notna().astype(float)
    if medians is None:
        medians = df.groupby("position")[PROD_FEATURES].median()
    for c in PROD_FEATURES:
        df[c] = df[c].fillna(df.position.map(medians[c]))
        df[c] = df[c].fillna(0.0)  # position absent from train medians
    return df, medians


def fit_per_position(train: pd.DataFrame, val: pd.DataFrame
                     ) -> dict[str, tuple[Ridge, float]]:
    """Inner-validated lambda per position, refit on train+val."""
    cols = ["b1_rate"] + PROD_FEATURES + ["has_history"]
    models = {}
    for pos, g in train.groupby("position"):
        gv = val[val.position == pos]
        best, best_mae = None, np.inf
        for lam in LAMBDAS:
            r = Ridge().fit(g[cols].to_numpy(float), g.rate.to_numpy(float), lam)
            if len(gv):
                mae = np.abs(r.predict(gv[cols].to_numpy(float))
                             - gv.rate.to_numpy(float)).mean()
            else:
                mae = 0.0
            if mae < best_mae:
                best, best_mae = lam, mae
        full = pd.concat([g, gv], ignore_index=True)
        r = Ridge().fit(full[cols].to_numpy(float), full.rate.to_numpy(float),
                        best)
        models[pos] = (r, best)
    return models


# ---------------------------------------------------------------------------
# training-pair construction (mirrors B1's, plus features)
# ---------------------------------------------------------------------------

def make_pairs(con, seasons, league_id, curve, cache) -> list[pd.DataFrame]:
    cols = ["b1_rate"] + PROD_FEATURES + ["has_history"]
    out = []
    for s in seasons:
        for ao in as_of_grid(con, s):
            f = cache.setdefault((s, ao), features_with_rank(con, ao, s))
            f = f[f.pos_rank.notna()].copy()
            b1 = b1_predict(f, curve).rename(columns={"yhat_total": "b1_total"})
            f = f.merge(b1[["sleeper_id", "b1_total"]], on="sleeper_id")
            f["b1_rate"] = f.b1_total / f.weeks_remaining.clip(lower=1)
            r = realized(con, ao, s, league_id)
            f = f.merge(r, on="sleeper_id", how="left")
            f["real_total"] = f.real_total.fillna(0.0)
            f["rate"] = f.real_total / f.weeks_remaining.clip(lower=1)
            f["as_of"] = ao
            f["grid_week"] = GRID_WEEKS[as_of_grid(con, s).index(ao)]
            out.append(f)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/dynasty.db")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    con.executescript(DDL)
    con.executescript("""CREATE TABLE IF NOT EXISTS model_coefficients (
        model_id TEXT, test_season INTEGER, position TEXT, feature TEXT,
        beta REAL, lam REAL,
        PRIMARY KEY (model_id, test_season, position, feature))""")
    league_id = con.execute("SELECT league_id FROM outcomes_provenance "
                            "WHERE is_canonical=1").fetchone()[0]
    cache: dict = {}
    pooled, coefs, eval_rows = [], [], []
    cols = ["b1_rate"] + PROD_FEATURES + ["has_history"]

    for S in TEST_SEASONS:
        train_seasons = list(range(2019, S))
        curve = train_b1_curve(con, train_seasons, league_id, cache)
        pairs = make_pairs(con, train_seasons, league_id, curve, cache)
        df, med = assemble_xy(pairs, None)
        # inner split: last train season is validation for lambda
        val_season = max(train_seasons)
        df["szn"] = df.as_of.str.slice(0, 4).astype(int)
        tr, va = df[df.szn < val_season], df[df.szn >= val_season]
        if tr.empty:                      # S=2020: only one train season
            tr, va = va, va.iloc[0:0]
        models = fit_per_position(tr, va)
        con.execute("INSERT OR REPLACE INTO model_runs "
                    "(model_id, train_window, grid) VALUES (?,?,?)",
                    (MODEL_ID, f"seasons<{S}", str(GRID_WEEKS)))

        test_pairs = make_pairs(con, [S], league_id, curve, cache)
        tf, _ = assemble_xy(test_pairs, med)   # TRAIN medians — no leakage
        frames = []
        for pos, g in tf.groupby("position"):
            if pos not in models:
                continue
            r, lam = models[pos]
            g = g.copy()
            g["m1_rate"] = r.predict(g[cols].to_numpy(float))
            g["yhat_total"] = g.m1_rate * g.weeks_remaining
            g["yhat_ppg"] = np.nan
            frames.append(g)
            coefs.append(pd.Series(r.beta, index=cols, name=(S, pos, lam)))
        tf = pd.concat(frames, ignore_index=True)
        for ao, g in tf.groupby("as_of"):
            log_predictions(con, MODEL_ID, ao,
                            g[["sleeper_id", "yhat_total", "yhat_ppg"]])
        tf["ae_m1"] = (tf.yhat_total - tf.real_total).abs()
        tf["ae_b1"] = (tf.b1_total - tf.real_total).abs()
        pooled.append(tf)
        for pos, g in tf.groupby("position"):
            skill = 1 - g.ae_m1.mean() / g.ae_b1.mean()
            eval_rows += [
                (MODEL_ID, S, "ros", pos, "mae_total",
                 float(g.ae_m1.mean()), len(g)),
                (MODEL_ID, S, "ros", pos, "skill_vs_b1", float(skill), len(g))]
        print(f"{S}: n={len(tf)}, skill_vs_B1="
              f"{1 - tf.ae_m1.mean() / tf.ae_b1.mean():+.3f}")

    con.executemany("INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?,?,?,?)",
                    eval_rows)
    con.commit()

    allf = pd.concat(pooled, ignore_index=True)
    print("\n=== m1 (ridge: B1 + production) vs B1, ros total_pts, "
          "2020-2025 pooled ===")
    tab = (allf.groupby("position")
           .apply(lambda g: pd.Series({
               "n": len(g), "MAE_B1": g.ae_b1.mean(), "MAE_m1": g.ae_m1.mean(),
               "skill_m1_vs_B1": 1 - g.ae_m1.mean() / g.ae_b1.mean()}),
               include_groups=False))
    print(tab.round(3).to_string())
    print("\nby grid week (how the production edge accrues in-season):")
    wk = (allf.groupby("grid_week")
          .apply(lambda g: pd.Series({
              "skill_m1_vs_B1": 1 - g.ae_m1.mean() / g.ae_b1.mean(),
              "n": len(g)}), include_groups=False))
    print(wk.round(3).to_string())
    mae1, maeb = allf.ae_m1.mean(), allf.ae_b1.mean()
    print(f"\npooled: MAE_B1={maeb:.2f}  MAE_m1={mae1:.2f}  "
          f"skill={1 - mae1 / maeb:.3f}")

    rng = np.random.default_rng(0)
    by_player = {pid: g[["ae_m1", "ae_b1"]].to_numpy()
                 for pid, g in allf.groupby("sleeper_id")}
    pids = list(by_player)
    skills = []
    for _ in range(1000):
        take = rng.choice(len(pids), size=len(pids), replace=True)
        arr = np.concatenate([by_player[pids[i]] for i in take])
        skills.append(1 - arr[:, 0].mean() / arr[:, 1].mean())
    lo, hi = np.percentile(skills, [2.5, 97.5])
    print(f"player-block bootstrap 95% CI: [{lo:.3f}, {hi:.3f}]")

    # pre-registered split, not a fishing expedition: rank should dominate
    # preseason (week-1 as_of) and production should only matter once
    # in-season data exists — test the in-season grid separately.
    ins = allf[allf.grid_week > 1]
    by_p = {pid: g[["ae_m1", "ae_b1"]].to_numpy()
            for pid, g in ins.groupby("sleeper_id")}
    ps = list(by_p)
    sk = []
    for _ in range(1000):
        take = rng.choice(len(ps), size=len(ps), replace=True)
        arr = np.concatenate([by_p[ps[i]] for i in take])
        sk.append(1 - arr[:, 0].mean() / arr[:, 1].mean())
    l2, h2 = np.percentile(sk, [2.5, 97.5])
    print(f"IN-SEASON only (weeks 5/9/13): skill="
          f"{1 - ins.ae_m1.mean() / ins.ae_b1.mean():.3f}, "
          f"95% CI [{l2:.3f}, {h2:.3f}], n={len(ins)}")

    # ---- persist for the Model Lab --------------------------------------------
    rows = [
        (MODEL_ID, 0, "ros", "ALL", "mae_total", float(mae1), len(allf)),
        (MODEL_ID, 0, "ros", "ALL", "skill_vs_b1",
         float(1 - mae1 / maeb), len(allf)),
        (MODEL_ID, 0, "ros", "ALL", "skill_vs_b1_ci_lo", float(lo), len(allf)),
        (MODEL_ID, 0, "ros", "ALL", "skill_vs_b1_ci_hi", float(hi), len(allf)),
        (MODEL_ID, 0, "ros", "ALL", "skill_vs_b1_inseason",
         float(1 - ins.ae_m1.mean() / ins.ae_b1.mean()), len(ins)),
        (MODEL_ID, 0, "ros", "ALL", "skill_vs_b1_inseason_ci_lo",
         float(l2), len(ins)),
        (MODEL_ID, 0, "ros", "ALL", "skill_vs_b1_inseason_ci_hi",
         float(h2), len(ins)),
    ]
    for w, g in allf.groupby("grid_week"):
        rows.append((MODEL_ID, 0, "ros", "ALL", f"skill_vs_b1_wk{int(w)}",
                     float(1 - g.ae_m1.mean() / g.ae_b1.mean()), len(g)))
    for pos, g in allf.groupby("position"):
        rows.append((MODEL_ID, 0, "ros", pos, "skill_vs_b1",
                     float(1 - g.ae_m1.mean() / g.ae_b1.mean()), len(g)))
        rows.append((MODEL_ID, 0, "ros", pos, "mae_total",
                     float(g.ae_m1.mean()), len(g)))
    con.executemany("INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?,?,?,?)",
                    rows)
    con.commit()

    cf = pd.DataFrame(coefs)
    cf.index = pd.MultiIndex.from_tuples(cf.index,
                                         names=["season", "pos", "lambda"])
    print("\nmean standardized coefficients (2020-2025), per position:")
    print(cf.groupby("pos").mean().round(2).T.to_string())
    coef_rows = []
    for (szn, pos, lam), beta in zip(cf.index, cf.to_numpy()):
        for feat, b in zip(cf.columns, beta):
            coef_rows.append((MODEL_ID, int(szn), pos, feat, float(b),
                              float(lam)))
    con.executemany("INSERT OR REPLACE INTO model_coefficients "
                    "VALUES (?,?,?,?,?,?)", coef_rows)
    con.commit()
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
