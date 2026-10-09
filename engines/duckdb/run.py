"""Run the baseline tables against the local `duckdb` CLI, parse the
`Total Files Read:` line from EXPLAIN ANALYZE, and print a result matrix.

Requires duckdb >= 1.5.6 (Iceberg + spatial extensions auto-installed on first
use). Run from the repo root after `python -m testbed.<name>` has built each
table.

What the V3 rows encode (DuckDB 1.5.6, iceberg extension 890b78a9c):
  - `ST_Intersects(geom, env)` evaluates correctly (1000 rows) but reads all
    10 files: duckdb-spatial does not derive a bbox pre-filter from it, so
    the manifest geometry bounds are never consulted.
  - `ST_Intersects_Extent(geom, env)` (or `geom && env`) is the bbox-only
    predicate the Iceberg planner can push to the manifest's per-file
    geometry bounds (duckdb-iceberg PR #1030): 1/10 files.
  - `v3_geography` still fails in the Iceberg type mapping
    (`Not implemented Error: Geography support`), although DuckDB reads the
    same parquet natively via `read_parquet`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CA_ENVELOPE = "ST_MakeEnvelope(-125, 32, -115, 42)"
MIN_DUCKDB = (1, 5, 6)

# (case label, metadata.json relative path, query SQL, expected files read
#  (None = the query is expected to error), behavior label)
CASES = [
    (
        "v2_flat_columns",
        REPO / "data" / "v2_flat_columns" / "metadata" / "v1.metadata.json",
        "WHERE xmin <= -118 AND xmax >= -125 AND ymin <= 40 AND ymax >= 37",
        1,
        "manifest pruning works for top-level numeric columns",
    ),
    (
        "v2_bbox_struct",
        REPO / "data" / "v2_bbox_struct" / "metadata" / "v1.metadata.json",
        "WHERE bbox.xmin <= -118 AND bbox.xmax >= -125 AND bbox.ymin <= 40 AND bbox.ymax >= 37",
        10,
        "struct-field predicates don't push to manifest bounds",
    ),
    (
        "v3_geometry/intersects",
        REPO / "data" / "v3_geometry" / "metadata" / "v1.metadata.json",
        f"WHERE ST_Intersects(geom, {CA_ENVELOPE})",
        10,
        "N3: correct rows, but no bbox pre-filter is derived -> full scan",
    ),
    (
        "v3_geometry/extent",
        REPO / "data" / "v3_geometry" / "metadata" / "v1.metadata.json",
        f"WHERE ST_Intersects_Extent(geom, {CA_ENVELOPE})",
        1,
        "N4: manifest geometry-bound pruning (packed_xy_le decoded)",
    ),
    (
        "v3_geography/extent",
        REPO / "data" / "v3_geography" / "metadata" / "v1.metadata.json",
        f"WHERE ST_Intersects_Extent(geog, {CA_ENVELOPE})",
        None,
        "Iceberg-layer gap: `Not implemented Error: Geography support`",
    ),
]


def files_read(output: str) -> int | None:
    m = re.search(r"Total Files Read:\s*(\d+)", output)
    return int(m.group(1)) if m else None


def run_one(metadata_path: Path, query_clause: str) -> tuple[str, int | None]:
    if not metadata_path.exists():
        return (f"missing metadata: {metadata_path}", None)
    sql = (
        "INSTALL iceberg; INSTALL spatial; LOAD iceberg; LOAD spatial; "
        f"EXPLAIN ANALYZE SELECT COUNT(*) FROM iceberg_scan('{metadata_path}') {query_clause};"
    )
    # Error output may echo raw manifest bytes (non-UTF-8); decode leniently.
    proc = subprocess.run(["duckdb", "-c", sql], capture_output=True, timeout=120)
    output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    return (output, files_read(output))


def main() -> int:
    if shutil.which("duckdb") is None:
        print("duckdb CLI not found on PATH. brew install duckdb (>= 1.5.6).", file=sys.stderr)
        return 1

    proc = subprocess.run(["duckdb", "--version"], capture_output=True, text=True)
    print(f"duckdb: {proc.stdout.strip()}")
    m = re.match(r"v(\d+)\.(\d+)\.(\d+)", proc.stdout.strip())
    if m is None or tuple(map(int, m.groups())) < MIN_DUCKDB:
        print("expected results are recorded for duckdb >= 1.5.6; upgrade the CLI.", file=sys.stderr)
        return 1
    print()
    print(f"{'case':24} {'expected':>10} {'actual':>10}  notes")
    print("-" * 80)

    all_ok = True
    for name, metadata, query, expected, label in CASES:
        output, actual = run_one(metadata, query)
        if expected is None:
            ok = actual is None and "Error" in output
        else:
            ok = actual == expected
        status = " " if ok else "!"
        actual_str = "ERR" if actual is None else str(actual)
        expected_str = "errors" if expected is None else str(expected)
        print(f"{status} {name:22} {expected_str:>10} {actual_str:>10}  {label}")
        if not ok:
            all_ok = False
            print("  ---- output ----")
            for line in output.splitlines()[-15:]:
                print(f"  {line}")

    return 0 if all_ok else 2


if __name__ == "__main__":
    sys.exit(main())
