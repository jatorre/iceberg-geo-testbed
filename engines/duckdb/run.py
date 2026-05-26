"""Run the three baseline tables against the local `duckdb` CLI, parse the
`Total Files Read:` line from EXPLAIN ANALYZE, and print a result matrix.

Requires duckdb ≥ 1.5.3 (Iceberg + spatial extensions auto-installed on first
use). Run from the repo root after `python -m testbed.<name>` has built each
table.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# (table_name, metadata.json relative path, query SQL, expected files read,
#  expected behavior label)
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
        "v3_geometry",
        REPO / "data" / "v3_geometry" / "metadata" / "v1.metadata.json",
        "WHERE ST_Intersects(geom, ST_MakeEnvelope(-125, 32, -115, 42))",
        None,  # bound deserializer crashes before pruning step runs
        "L2 readback (geom typed, ST_AsText works); manifest-bound deser missing → no spatial pruning",
    ),
]


def files_read(output: str) -> int | None:
    m = re.search(r"Total Files Read:\s*(\d+)", output)
    return int(m.group(1)) if m else None


def run_one(metadata_path: Path, query_clause: str) -> tuple[str, int | None]:
    if not metadata_path.exists():
        return (f"missing metadata: {metadata_path}", None)
    sql_extras = "LOAD iceberg; LOAD spatial;"
    sql = (
        f"INSTALL iceberg; INSTALL spatial; {sql_extras} "
        f"EXPLAIN ANALYZE SELECT COUNT(*) FROM iceberg_scan('{metadata_path}') {query_clause};"
    )
    # DuckDB's error output for the geometry-bound case includes the raw blob
    # bytes (non-UTF-8), so capture as bytes and decode with replace.
    proc = subprocess.run(["duckdb", "-c", sql], capture_output=True, timeout=60)
    output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    return (output, files_read(output))


def main() -> int:
    if shutil.which("duckdb") is None:
        print("duckdb CLI not found on PATH. brew install duckdb (≥ 1.5.3).", file=sys.stderr)
        return 1

    proc = subprocess.run(["duckdb", "--version"], capture_output=True, text=True)
    print(f"duckdb: {proc.stdout.strip()}")
    print()
    print(f"{'case':24} {'expected':>10} {'actual':>10}  notes")
    print("-" * 80)

    all_ok = True
    for name, metadata, query, expected, label in CASES:
        output, actual = run_one(metadata, query)
        ok = actual == expected if expected is not None else "FAIL" in output or "Error" in output
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
