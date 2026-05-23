"""Diff our hand-written manifest avro against Sedona's ground-truth.

For each table, compare:
  - the avro schema (the per-engine view of ManifestFile / DataFile)
  - per-data-file: path, record_count, file_size, lower_bounds, upper_bounds
  - the byte encoding of lower_bound / upper_bound for the geometry column

Run AFTER `engines/sedona/run.sh` has populated engines/sedona/work/.
"""

from __future__ import annotations

import sys
from pathlib import Path

import fastavro


REPO = Path(__file__).resolve().parents[2]
OURS_BASE = REPO / "data"
THEIRS_BASE = REPO / "engines" / "sedona" / "work" / "warehouse"


def find_manifest_avro(table_root: Path) -> Path | None:
    """Find the data manifest avro (not the manifest-list). The manifest-list
    references one or more manifest avros."""
    metas = list(table_root.glob("**/metadata/*.avro"))
    # Heuristic: manifests typically contain "manifest" in the name but not
    # "manifest-list".
    for p in metas:
        name = p.name.lower()
        if "manifest" in name and "list" not in name:
            return p
    return None


def read_manifest(path: Path) -> tuple[dict, list[dict]]:
    with open(path, "rb") as f:
        reader = fastavro.reader(f)
        schema = reader.writer_schema
        rows = list(reader)
    return schema, rows


def summarize_data_file(row: dict) -> dict:
    df = row.get("data_file") or row
    return {
        "file_path": df.get("file_path"),
        "record_count": df.get("record_count"),
        "file_size": df.get("file_size_in_bytes") or df.get("file_size"),
        "lower_bounds": dict(df.get("lower_bounds") or []) if df.get("lower_bounds") else None,
        "upper_bounds": dict(df.get("upper_bounds") or []) if df.get("upper_bounds") else None,
    }


def diff_table(name: str) -> None:
    print(f"\n========== {name} ==========")
    ours_root = OURS_BASE / name
    theirs_root = THEIRS_BASE / name

    ours_path = find_manifest_avro(ours_root)
    theirs_path = find_manifest_avro(theirs_root)
    print(f"ours:   {ours_path}")
    print(f"theirs: {theirs_path}")

    if not ours_path or not theirs_path:
        print("  one side is missing — skip")
        return

    ours_schema, ours_rows = read_manifest(ours_path)
    theirs_schema, theirs_rows = read_manifest(theirs_path)

    # Schema field-name diff
    def top_field_names(s):
        if isinstance(s, dict) and s.get("type") == "record":
            return [f["name"] for f in s["fields"]]
        return []

    of = set(top_field_names(ours_schema))
    tf = set(top_field_names(theirs_schema))
    print("\n-- schema --")
    print(f"  ours only:   {sorted(of - tf)}")
    print(f"  theirs only: {sorted(tf - of)}")
    print(f"  both:        {sorted(of & tf)}")

    # Per-file row diff
    print("\n-- rows --")
    print(f"  ours: {len(ours_rows)} rows, theirs: {len(theirs_rows)} rows")
    for r in ours_rows[:3]:
        s = summarize_data_file(r)
        print(f"  OURS   path={s['file_path']!s:60} rec={s['record_count']} bounds_lower={s['lower_bounds']}")
    for r in theirs_rows[:3]:
        s = summarize_data_file(r)
        print(f"  THEIRS path={s['file_path']!s:60} rec={s['record_count']} bounds_lower={s['lower_bounds']}")


def main() -> int:
    for name in ["v2_flat_columns", "v3_geometry"]:
        diff_table(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
