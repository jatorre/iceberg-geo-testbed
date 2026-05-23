"""Run the three baseline tables against BigQuery (BigLake external Iceberg)
and report the support level reached for each.

Prereqs: see `engines/bigquery/README.md`.
  - `gcloud auth login` (the runner shells out to `bq`)
  - The metadata is already at `gs://<BUCKET>/<table>/metadata/v1.metadata.json`
    — run `engines/bigquery/_setup.py` once if you've built fresh fixtures.

Support ladder per fixture:
  L0  table won't even register (CREATE EXTERNAL TABLE errors)
  L1  registers + SELECT * works (full-scan returns rows)
  L2  spatial predicate returns the correct rows
  L3  file-level pruning (only matching files read at manifest level)
  L4  row-group / page pruning further narrows inside surviving files
       (not currently measured in this script — would need page-index stats)

The pruning inference is byte-count-based. Each row in our fixtures is a
fixed width: 4 doubles (32 B) for v2_flat, 1 struct of 4 doubles (32 B) for
v2_struct, etc. 10 files × 1000 rows. So:
  bytes_per_file_per_predicate_columns = 1000 * 8 * N_cols
  bytes_all_files = bytes_per_file * 10
We compare `total_bytes_processed` against these.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT = os.environ.get("BQ_PROJECT", "cartobq")
LOCATION = os.environ.get("BQ_LOCATION", "US")
DATASET = os.environ.get("BQ_DATASET", "iceberg_geo_testbed")
CONNECTION = os.environ.get("BQ_CONNECTION", f"{PROJECT}.us.iceberg_connection")
BUCKET = os.environ.get("BUCKET", "cartobq-iceberg-geo-testbed")


@dataclass
class Case:
    name: str
    predicate: str
    expected_rows: int
    # number of double-equivalent columns the predicate touches per row.
    # used to predict the pruned vs. unpruned bytes.
    predicate_n_cols: int


CASES = [
    Case(
        name="v2_flat_columns",
        predicate="WHERE xmin <= -118 AND xmax >= -125 AND ymin <= 40 AND ymax >= 37",
        expected_rows=196,
        predicate_n_cols=4,
    ),
    Case(
        name="v2_bbox_struct",
        predicate=(
            "WHERE bbox.xmin <= -118 AND bbox.xmax >= -125 "
            "AND bbox.ymin <= 40 AND bbox.ymax >= 37"
        ),
        expected_rows=196,
        predicate_n_cols=4,
    ),
    Case(
        # The geom column carries WKB points; BigQuery would need to know
        # how to extract coords + apply ST_INTERSECTS. But since the type is
        # rejected at table creation, this predicate is never reached.
        name="v3_geometry",
        predicate=(
            "WHERE ST_INTERSECTS(ST_GEOGFROMWKB(geom), "
            "ST_MAKEPOLYGON(ST_GEOGFROMTEXT('"
            "LINESTRING(-125 32, -115 32, -115 42, -125 42, -125 32)"
            "')))"
        ),
        expected_rows=196,
        predicate_n_cols=1,
    ),
]


def bq(sql: str) -> tuple[int, str]:
    """Run a SQL statement via the bq CLI; return (returncode, combined output)."""
    proc = subprocess.run(
        [
            "bq",
            "query",
            "--use_legacy_sql=false",
            f"--project_id={PROJECT}",
            f"--location={LOCATION}",
            "--format=json",
            "--quiet",
            sql,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def create_table(case: Case) -> tuple[bool, str]:
    sql = (
        f"CREATE OR REPLACE EXTERNAL TABLE `{PROJECT}.{DATASET}.{case.name}` "
        f"WITH CONNECTION `{CONNECTION}` "
        f"OPTIONS (format='ICEBERG', "
        f"uris=['gs://{BUCKET}/{case.name}/metadata/v1.metadata.json'])"
    )
    rc, out = bq(sql)
    return rc == 0, out


def full_count(case: Case) -> tuple[bool, int | None, str]:
    rc, out = bq(f"SELECT COUNT(*) AS n FROM `{PROJECT}.{DATASET}.{case.name}`")
    if rc != 0:
        return False, None, out
    try:
        rows = json.loads(out)
        return True, int(rows[0]["n"]), out
    except Exception:
        return False, None, out


def probe(case: Case) -> tuple[bool, int | None, str]:
    rc, out = bq(
        f"SELECT COUNT(*) AS n FROM `{PROJECT}.{DATASET}.{case.name}` {case.predicate}"
    )
    if rc != 0:
        return False, None, out
    try:
        rows = json.loads(out)
        return True, int(rows[0]["n"]), out
    except Exception:
        return False, None, out


def last_query_bytes(case: Case) -> int | None:
    """Read total_bytes_processed for the most recent SELECT against this table."""
    sql = (
        f"SELECT total_bytes_processed FROM "
        f"`region-{LOCATION.lower()}.INFORMATION_SCHEMA.JOBS_BY_PROJECT` "
        f"WHERE creation_time > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE) "
        f"AND query LIKE '%{DATASET}.{case.name}%' "
        f"AND query NOT LIKE '%JOBS_BY_PROJECT%' "
        f"AND statement_type = 'SELECT' "
        f"ORDER BY creation_time DESC LIMIT 1"
    )
    rc, out = bq(sql)
    if rc != 0:
        return None
    try:
        rows = json.loads(out)
        return int(rows[0]["total_bytes_processed"])
    except Exception:
        return None


def assess(case: Case) -> dict:
    """Walk the ladder. Returns a dict with the case state."""
    state = {"case": case.name, "level": "L0", "notes": []}

    ok, out = create_table(case)
    if not ok:
        # The most informative line in bq's failure output is usually after
        # "Error while reading table:" or contains "error message:".
        lines = out.splitlines()
        msg = next(
            (ln.strip() for ln in lines if "error message" in ln.lower()),
            next((ln.strip() for ln in lines if "error" in ln.lower()), out.strip()),
        )
        state["notes"].append(f"CREATE failed: {msg[:240]}")
        return state
    state["notes"].append("CREATE ok")

    ok, n, out = full_count(case)
    if not ok or n is None:
        msg = next((ln for ln in out.splitlines() if "error" in ln.lower()), out.strip())
        state["notes"].append(f"SELECT COUNT(*) failed: {msg[:200]}")
        return state
    state["level"] = "L1"
    state["notes"].append(f"full-scan rows = {n}")

    ok, n, out = probe(case)
    if not ok or n is None:
        msg = next((ln for ln in out.splitlines() if "error" in ln.lower()), out.strip())
        state["notes"].append(f"predicate query failed: {msg[:200]}")
        return state
    if n != case.expected_rows:
        state["notes"].append(f"predicate rows = {n} (expected {case.expected_rows})")
        return state
    state["level"] = "L2"
    state["notes"].append(f"predicate rows = {n} ✓")

    bytes_one_file = 1000 * 8 * case.predicate_n_cols
    bytes_all_files = bytes_one_file * 10

    measured = last_query_bytes(case)
    if measured is None:
        state["notes"].append("bytes-processed lookup failed; can't infer pruning")
        return state

    files_inferred = round(measured / bytes_one_file) if bytes_one_file else None
    state["notes"].append(
        f"bytes_processed={measured} "
        f"(1 file ≈ {bytes_one_file}; all 10 ≈ {bytes_all_files}) "
        f"→ ~{files_inferred} files scanned"
    )

    if measured <= bytes_one_file * 1.5:
        state["level"] = "L3"
        state["notes"].append("manifest-level pruning works")
    else:
        state["notes"].append("no manifest-level pruning")

    return state


def main() -> int:
    # Ensure the dataset exists. Idempotent.
    bq(f"CREATE SCHEMA IF NOT EXISTS `{PROJECT}.{DATASET}`")

    print(f"BigQuery probe — project={PROJECT} dataset={DATASET} bucket=gs://{BUCKET}")
    print()
    print(f"{'case':22} {'level':>5}  notes")
    print("-" * 90)

    for case in CASES:
        s = assess(case)
        print(f"{s['case']:22} {s['level']:>5}  {s['notes'][-1]}")
        for n in s["notes"][:-1]:
            print(f"{'':22} {'':>5}  · {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
