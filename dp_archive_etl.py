"""
dp_archive_etl.py (v2) — materialize DynastyProcess values history into SQLite.

v2 fixes, responding to review:
  BUG A (crash): 17 knowledge_dates are shared by >1 commit (stale scrape_date
    re-commits + same-day pushes). v1's append hit the PK and aborted.
    Policy: LATEST COMMIT WINS per (knowledge_date, player_key) — commits are
    processed oldest-first, staged, deduped keep="last", written via
    INSERT OR REPLACE. commit_sha is NOT in the PK (would fan out downstream).
  BUG B (silent): the 23 pre-2020-05-04 "dyno era" commits use a different
    schema (mergename / dynoECR / dyno2QBECR, no fp_id) and were silently
    dropped, shrinking the realized horizon to mid-2020. v2 maps all 6 column
    signatures, and resolves identity per era:
      - fp_id era (2020-05-04+): join crosswalk on fantasypros_id
      - pre-fp_id era: join crosswalk on merge_name (DP's own normalized key,
        present in BOTH the dyno files and db_playerids.csv — a key join, not
        fuzzy matching). The pre-2020 match rate is REPORTED, not assumed.
    player_key = fp_id when present else 'mn:'+merge_name (stable PK either era).
  Also: "file absent at commit" vs "recognized file produced zero rows" are now
    distinguished — the second RAISES instead of passing quietly.

Dyno-era semantics: dynoECR -> ecr_1qb, dyno2QBECR -> ecr_2qb, dynpECR ->
ecr_pos. Values (value_1qb/2qb) are absent in 22 of 23 early commits — fine for
B1, which needs the RANK (DP value is a deterministic transform of rank anyway).

Bonus: materializes id_crosswalk(sleeper_id, gsis_id, fp_id, merge_name,
position, birthdate) from db_playerids.csv — required by build_features (the
warehouse has no birthdate; dim_players carries only integer age).

Usage:
    python dp_archive_etl.py --db etl/data/dynasty.db [--since 2019-01-01]
"""
from __future__ import annotations

import argparse
import io
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO_URL = "https://github.com/dynastyprocess/data.git"
FILE_PATH = "files/values-players.csv"
XWALK_URL = ("https://raw.githubusercontent.com/dynastyprocess/data/"
             "master/files/db_playerids.csv")
FP_ID_ERA_START = "2020-05-04"   # first commit carrying fp_id (verified)

DDL = """
CREATE TABLE IF NOT EXISTS dp_values_history (
    knowledge_date TEXT NOT NULL,  -- file's own scrape_date, else commit date
    player_key     TEXT NOT NULL,  -- fp_id, else 'mn:'+merge_name (pre-2020 era)
    commit_sha     TEXT NOT NULL,
    fp_id          TEXT,
    merge_name     TEXT,
    sleeper_id     TEXT,           -- resolved at load via crosswalk; NULL = unmatched
    player         TEXT,
    pos            TEXT,
    team           TEXT,
    age            REAL,
    draft_year     REAL,
    ecr_1qb        REAL,
    ecr_2qb        REAL,
    ecr_pos        REAL,
    value_1qb      REAL,
    value_2qb      REAL,
    PRIMARY KEY (knowledge_date, player_key)
);
CREATE INDEX IF NOT EXISTS ix_dpvh_kd  ON dp_values_history (knowledge_date);
CREATE INDEX IF NOT EXISTS ix_dpvh_sid ON dp_values_history (sleeper_id);

-- Processed-commit manifest. NOT derivable from dp_values_history: a commit
-- fully superseded by a later colliding commit leaves no rows behind, and
-- re-processing it would overwrite winner rows with older data.
CREATE TABLE IF NOT EXISTS dp_load_manifest (
    commit_sha TEXT PRIMARY KEY,
    loaded_at  TEXT
);

CREATE TABLE IF NOT EXISTS id_crosswalk (
    sleeper_id TEXT PRIMARY KEY,
    gsis_id    TEXT,
    fp_id      TEXT,
    merge_name TEXT,
    position   TEXT,
    birthdate  TEXT
);
"""

# Per-column alias map covering all 6 observed signatures (2019-04 .. now).
RENAMES = {
    # identity / labels
    "fp_id": "fp_id", "id_fp": "fp_id",
    "mergename": "merge_name",
    "player": "player", "name": "player",
    "pos": "pos", "position": "pos",
    "team": "team", "tm": "team",
    "age": "age", "draft_year": "draft_year",
    # modern ranks/values
    "ecr_1qb": "ecr_1qb", "ecr_2qb": "ecr_2qb", "ecr_pos": "ecr_pos",
    "value_1qb": "value_1qb", "value_2qb": "value_2qb",
    # dyno era (2019-04 .. 2020-04)
    "dynoECR": "ecr_1qb", "dyno2QBECR": "ecr_2qb", "dynpECR": "ecr_pos",
    "1QBValue": "value_1qb", "2QB Value": "value_2qb",
    "scrape_date": "scrape_date",
}
KEEP = ["fp_id", "merge_name", "player", "pos", "team", "age", "draft_year",
        "ecr_1qb", "ecr_2qb", "ecr_pos", "value_1qb", "value_2qb"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


def ensure_repo(workdir: Path) -> Path:
    repo = workdir / "dp-data"
    if not repo.exists():
        subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout",
                        REPO_URL, str(repo)], check=True)
    else:
        subprocess.run(["git", "-C", str(repo), "fetch", "--quiet"], check=True)
    return repo


def list_snapshots(repo: Path, since: str) -> list[tuple[str, str]]:
    out = _git(repo, "log", "--reverse", "--format=%H %ad",
               "--date=format:%Y-%m-%d", f"--since={since}", "--", FILE_PATH)
    return [tuple(line.split()) for line in out.splitlines() if line.strip()]


def load_crosswalk(con: sqlite3.Connection) -> pd.DataFrame:
    xw = pd.read_csv(XWALK_URL, dtype=str)
    xw = xw[["sleeper_id", "gsis_id", "fantasypros_id", "merge_name",
             "position", "birthdate"]].rename(columns={"fantasypros_id": "fp_id"})
    xw = xw[xw.sleeper_id.notna()].drop_duplicates("sleeper_id")
    con.execute("DELETE FROM id_crosswalk")
    xw.to_sql("id_crosswalk", con, if_exists="append", index=False)
    return xw


def load_snapshot(repo: Path, sha: str, commit_date: str) -> pd.DataFrame | None:
    """None = file absent at this commit. Raises if a recognized file yields
    zero rows (the v1 silent-swallow)."""
    try:
        raw = _git(repo, "show", f"{sha}:{FILE_PATH}")
    except subprocess.CalledProcessError:
        return None
    df = pd.read_csv(io.StringIO(raw))
    df = df.rename(columns={c: RENAMES[c] for c in df.columns if c in RENAMES})
    df = df.loc[:, ~df.columns.duplicated()]
    for col in KEEP:
        if col not in df.columns:
            df[col] = None

    kd = commit_date
    if "scrape_date" in df.columns and df["scrape_date"].notna().any():
        kd = str(pd.to_datetime(df["scrape_date"].dropna().iloc[0]).date())

    out = df[KEEP].copy()
    out["fp_id"] = out["fp_id"].astype("string")
    out["merge_name"] = out["merge_name"].astype("string").str.strip().str.lower()
    # 7th signature (2020-04-27..05-01): modern columns but NO fp_id and NO
    # mergename. Derive merge_name from player using DP's own normalization
    # (lowercase, strip punctuation, drop Jr/Sr/II.. suffixes) so the same
    # crosswalk merge_name join applies.
    need = out["merge_name"].isna() & out["player"].notna()
    if need.any():
        out.loc[need, "merge_name"] = (
            out.loc[need, "player"].astype(str).str.lower()
            .str.replace(r"[^a-z ]", "", regex=True)
            .str.replace(r"\s+(jr|sr|ii|iii|iv|v)$", "", regex=True)
            .str.strip())
    out["player_key"] = out["fp_id"].where(
        out["fp_id"].notna(), "mn:" + out["merge_name"])
    out = out[out["player_key"].notna()]
    out.insert(0, "commit_sha", sha)
    out.insert(0, "knowledge_date", kd)
    out = out.drop_duplicates(subset=["knowledge_date", "player_key"], keep="last")
    if out.empty:
        raise RuntimeError(
            f"Recognized values file at {sha[:8]} ({commit_date}) produced 0 "
            f"rows — alias map is missing a column signature. Raw columns: "
            f"{pd.read_csv(io.StringIO(raw), nrows=0).columns.tolist()}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="etl/data/dynasty.db")
    ap.add_argument("--since", default="2019-01-01")
    ap.add_argument("--workdir", default=".cache")
    args = ap.parse_args()

    Path(args.workdir).mkdir(parents=True, exist_ok=True)
    repo = ensure_repo(Path(args.workdir))
    con = sqlite3.connect(args.db)
    con.executescript(DDL)
    xw = load_crosswalk(con)

    have = {r[0] for r in con.execute(
        "SELECT commit_sha FROM dp_load_manifest")}
    snaps = list_snapshots(repo, args.since)
    todo = [(sha, d) for sha, d in snaps if sha not in have]
    print(f"{len(snaps)} snapshots in history; {len(todo)} new to load.")

    frames, absent = [], 0
    for sha, d in todo:
        df = load_snapshot(repo, sha, d)
        if df is None:
            absent += 1
            continue
        frames.append(df)
    if not frames:
        print("Nothing to load.")
        con.close()
        return 0

    stage = pd.concat(frames, ignore_index=True)
    # BUG A fix: latest commit wins across colliding knowledge_dates.
    # frames are oldest-first, so keep="last" keeps the freshest correction.
    stage = stage.drop_duplicates(subset=["knowledge_date", "player_key"],
                                  keep="last")

    # Identity resolution (BUG B fix): fp_id era joins on fp_id; pre-fp_id era
    # joins on merge_name. Crosswalk merge_name duplicates (same normalized
    # name, different players) are ambiguous -> excluded from the name join.
    by_fp = xw[xw.fp_id.notna()][["fp_id", "sleeper_id"]].drop_duplicates("fp_id")
    mn_unique = xw[xw.merge_name.notna()].drop_duplicates("merge_name", keep=False)
    stage = stage.merge(by_fp.rename(columns={"sleeper_id": "sid_fp"}),
                        on="fp_id", how="left")
    stage = stage.merge(
        mn_unique[["merge_name", "sleeper_id"]].rename(
            columns={"sleeper_id": "sid_mn"}),
        on="merge_name", how="left")
    stage["sleeper_id"] = stage["sid_fp"].where(stage["sid_fp"].notna(),
                                                stage["sid_mn"])
    stage = stage.drop(columns=["sid_fp", "sid_mn"])

    cols = ["knowledge_date", "player_key", "commit_sha", "fp_id", "merge_name",
            "sleeper_id", "player", "pos", "team", "age", "draft_year",
            "ecr_1qb", "ecr_2qb", "ecr_pos", "value_1qb", "value_2qb"]
    stage = stage[cols].astype(object).where(stage[cols].notna(), None)
    con.executemany(
        f"INSERT OR REPLACE INTO dp_values_history ({','.join(cols)}) "
        f"VALUES ({','.join('?' * len(cols))})",
        stage.itertuples(index=False, name=None))
    con.executemany(
        "INSERT OR IGNORE INTO dp_load_manifest VALUES (?, datetime('now'))",
        [(sha,) for sha, _ in todo])
    con.commit()

    # ---- honest load report --------------------------------------------------
    n, lo, hi, nsnap = con.execute(
        "SELECT COUNT(*), MIN(knowledge_date), MAX(knowledge_date), "
        "COUNT(DISTINCT knowledge_date) FROM dp_values_history").fetchone()
    pre, pre_matched = con.execute(
        "SELECT COUNT(*), SUM(sleeper_id IS NOT NULL) FROM dp_values_history "
        "WHERE knowledge_date < ?", (FP_ID_ERA_START,)).fetchone()
    post, post_matched = con.execute(
        "SELECT COUNT(*), SUM(sleeper_id IS NOT NULL) FROM dp_values_history "
        "WHERE knowledge_date >= ?", (FP_ID_ERA_START,)).fetchone()
    print(f"Table: {n} rows, {nsnap} distinct knowledge_dates, {lo} .. {hi}. "
          f"File-absent commits skipped: {absent}.")
    if pre:
        print(f"  pre-fp_id era  (<{FP_ID_ERA_START}): {pre} rows, "
              f"sleeper match {pre_matched / pre:.1%} (merge_name join)")
    if post:
        print(f"  fp_id era     (>={FP_ID_ERA_START}): {post} rows, "
              f"sleeper match {post_matched / post:.1%} (fp_id join)")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
