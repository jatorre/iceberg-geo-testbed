"""Snowflake engine runner — probes all four fixtures, reports L0–L4 and
N1–N4 (where applicable) for each.

Prereqs:
  - Credentials in ~/.config/iceberg-geo-testbed/snowflake.txt (see _creds.py)
  - External volume + catalog integration provisioned (see _provision.py)
  - Storage SA granted both objectAdmin AND legacyBucketReader on the
    backing bucket — `storage.buckets.get` is what Snowflake's
    Iceberg-table provisioning actually needs (Snowflake support
    confirmed this; the canonical IAM trap that 091369 hides).

Use:
  python engines/snowflake/run.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "engines" / "snowflake"))

import snowflake.connector  # noqa: E402

from _creds import load  # noqa: E402


DB = "TESTBED"
SCHEMA = "PUBLIC2"
EXTERNAL_VOLUME = "ICEBERG_VOL_FRESH"
CATALOG = "ICEBERG_CAT_FRESH"


@dataclass
class Case:
    name: str
    predicate: str
    expected_rows: int
    is_v3: bool = False


CASES = [
    Case(
        name="v2_flat_columns",
        predicate="WHERE xmin <= -118 AND xmax >= -125 AND ymin <= 40 AND ymax >= 37",
        expected_rows=196,
    ),
    Case(
        name="v2_bbox_struct",
        # Snowflake exposes Iceberg STRUCT columns as OBJECT and uses
        # variant `:` access with explicit casts (vs. dot notation in
        # other engines).
        predicate=(
            "WHERE bbox:xmin::FLOAT <= -118 AND bbox:xmax::FLOAT >= -125 "
            "AND bbox:ymin::FLOAT <= 40 AND bbox:ymax::FLOAT >= 37"
        ),
        expected_rows=196,
    ),
    Case(
        name="v2_geo_convention",
        predicate=(
            "WHERE fp_xmin <= -118 AND fp_xmax >= -125 "
            "AND fp_ymin <= 40 AND fp_ymax >= 37"
        ),
        expected_rows=196,
    ),
    Case(
        name="v3_geometry",
        # Snowflake's spatial syntax for V3 geometry — if the type binds
        # we can use ST_INTERSECTS directly on the typed column.
        predicate=(
            "WHERE ST_INTERSECTS(geom, "
            "TO_GEOMETRY('POLYGON((-125 32, -115 32, -115 42, -125 42, -125 32))'))"
        ),
        expected_rows=196,
        is_v3=True,
    ),
    Case(
        name="v3_geometry_lineage",
        # Companion to v3_geometry with row-lineage on (spec-compliant
        # _row_id and _last_updated_sequence_number columns in parquet).
        predicate=(
            "WHERE ST_INTERSECTS(geom, "
            "TO_GEOMETRY('POLYGON((-125 32, -115 32, -115 42, -125 42, -125 32))'))"
        ),
        expected_rows=196,
        is_v3=True,
    ),
]


def assess(cur, case: Case) -> dict:
    state = {"case": case.name, "level": "L0", "notes": []}

    # CREATE OR REPLACE the iceberg table
    try:
        cur.execute(f"DROP TABLE IF EXISTS {case.name}")
    except Exception:
        pass
    try:
        cur.execute(
            f"""CREATE OR REPLACE ICEBERG TABLE {case.name}
                EXTERNAL_VOLUME = '{EXTERNAL_VOLUME}'
                CATALOG = '{CATALOG}'
                METADATA_FILE_PATH = '{case.name}/metadata/v1.metadata.json'"""
        )
    except Exception as e:
        msg = str(e).splitlines()[0]
        state["notes"].append(f"CREATE failed: {msg[:240]}")
        return state
    state["notes"].append("CREATE ok")

    # Full-scan count (L1)
    try:
        cur.execute(f"SELECT COUNT(*) FROM {case.name}")
        n = cur.fetchone()[0]
    except Exception as e:
        msg = str(e).splitlines()[0]
        state["notes"].append(f"COUNT failed: {msg[:240]}")
        return state
    state["level"] = "L1"
    state["notes"].append(f"full-scan rows = {n}")

    # Predicate (L2)
    try:
        cur.execute(f"SELECT COUNT(*) FROM {case.name} {case.predicate}")
        n = cur.fetchone()[0]
    except Exception as e:
        msg = str(e).splitlines()[0]
        state["notes"].append(f"predicate failed: {msg[:240]}")
        return state
    if n != case.expected_rows:
        state["notes"].append(f"predicate rows = {n} (expected {case.expected_rows})")
        return state
    state["level"] = "L2"
    state["notes"].append(f"predicate rows = {n} ✓")

    # File pruning (L3) — INFORMATION_SCHEMA.QUERY_HISTORY exposes
    # BYTES_SCANNED but not partitions_scanned (that's in ACCOUNT_USAGE,
    # delayed up to 45min). Compare predicate-scan bytes against a
    # full-column-scan baseline: a 1-of-10-files prune should land
    # at ~1/10 of the baseline. Each parquet file is ~30-40 KB.
    try:
        cur.execute(
            f"""SELECT bytes_scanned
                FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY(RESULT_LIMIT => 30))
                WHERE query_text ILIKE '%FROM {case.name} WHERE%'
                  AND error_code IS NULL
                ORDER BY start_time DESC LIMIT 1"""
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            bytes_scanned = row[0]
            state["notes"].append(f"bytes_scanned={bytes_scanned}")
            # Heuristic: single-file scan is well below 100 KB for our
            # synthetic fixtures (10 files × ~30 KB each). If the
            # predicate scanned < 1/4 of an all-files baseline (~100 KB),
            # we're pruning.
            if bytes_scanned < 100_000:
                state["level"] = "L3"
                state["notes"].append("(single-file pruning inferred)")
        else:
            state["notes"].append("bytes_scanned not available in QUERY_HISTORY")
    except Exception as e:
        msg = str(e).splitlines()[0]
        state["notes"].append(f"scan-stats lookup failed: {msg[:200]}")

    return state


def main() -> int:
    creds = load()
    conn = snowflake.connector.connect(
        **creds, role="ACCOUNTADMIN", warehouse="COMPUTE_WH", database=DB, schema=SCHEMA,
    )
    cur = conn.cursor()

    # Confirm version + region
    cur.execute("SELECT CURRENT_VERSION(), CURRENT_REGION()")
    v, r = cur.fetchone()
    print(f"Snowflake {v} on {r}")
    print(f"external volume: {EXTERNAL_VOLUME}  catalog: {CATALOG}")
    print()
    print(f"{'case':22} {'level':>5}  notes")
    print("-" * 100)

    for case in CASES:
        s = assess(cur, case)
        print(f"{s['case']:22} {s['level']:>5}  {s['notes'][-1]}")
        for n in s["notes"][:-1]:
            print(f"{'':22} {'':>5}  · {n}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
