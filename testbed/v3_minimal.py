"""Minimal V3 Iceberg fixture — no geometry, just `id` STRING + `n` INT.

The geo blog needs to distinguish two questions:

  1. Does engine X have a working V3 reader at all?
  2. Does engine X support V3 *geometry* specifically?

`v3_geometry.py` answers (2). This fixture answers (1) — same V3
metadata.json + V3 manifest avro shape as v3_geometry, but with
non-geometry columns only. If an engine reads this fine but errors
on v3_geometry, geometry is the gap. If it errors on this too, V3
itself isn't shipped on that engine.

Built deterministically (same `stable_seed` as the other fixtures) so
the parquet bytes are reproducible across rebuilds.
"""

from __future__ import annotations

import random
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.schema import Schema
from pyiceberg.types import IntegerType, NestedField, StringType

from .common import REGIONS, stable_seed
from ._static_catalog import write_static_catalog

ROOT = Path(__file__).parent.parent / "data" / "v3_minimal"


def _field_meta(field_id: int) -> dict:
    return {"PARQUET:field_id": str(field_id)}


PY_SCHEMA = Schema(
    NestedField(1, "id", StringType(), required=False),
    NestedField(2, "n", IntegerType(), required=False),
)

ARROW_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string(), nullable=True, metadata=_field_meta(1)),
        pa.field("n", pa.int32(), nullable=True, metadata=_field_meta(2)),
    ]
)


def _write_parquet(region) -> Path:
    rng = random.Random(stable_seed(region.name))
    rows = 1000
    ids = [f"{region.name}-{i}" for i in range(rows)]
    ns = [rng.randint(0, 1_000_000) for _ in range(rows)]
    table = pa.table(
        {"id": pa.array(ids, type=pa.string()), "n": pa.array(ns, type=pa.int32())},
        schema=ARROW_SCHEMA,
    )
    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{region.name}.parquet"
    pq.write_table(table, out, compression="zstd",
                   store_schema=True, write_statistics=True)
    return out


def build(*, location_uri: str | None = None, meta_dir_name: str = "metadata") -> Path:
    data_files = []
    for region in REGIONS:
        p = _write_parquet(region)
        # Same per-file metric shape as v3_geometry: populated value_counts /
        # null_value_counts + lower/upper bounds for both columns. Iceberg's
        # int bounds are 4-byte little-endian; string bounds are UTF-8 bytes.
        first_id, last_id = f"{region.name}-0".encode(), f"{region.name}-999".encode()
        data_files.append(
            {
                "path": f"data/{region.name}.parquet",
                "size": p.stat().st_size,
                "rows": 1000,
                # bounds present only on `id` to keep things minimal; per-file
                # int range isn't deterministic anyway since `n` is random.
                "lower": {1: first_id},
                "upper": {1: last_id},
                "value_counts": {1: 1000, 2: 1000},
                "null_value_counts": {1: 0, 2: 0},
            }
        )

    return write_static_catalog(
        table_root=ROOT,
        iceberg_schema=PY_SCHEMA,
        schema_json_fields=[
            {"id": 1, "name": "id", "required": False, "type": "string"},
            {"id": 2, "name": "n", "required": False, "type": "int"},
        ],
        name_mapping=[
            {"field-id": 1, "names": ["id"]},
            {"field-id": 2, "names": ["n"]},
        ],
        data_files=data_files,
        format_version_in_metadata=3,
        location_uri=location_uri,
        meta_dir_name=meta_dir_name,
        row_lineage=None,
    )


if __name__ == "__main__":
    path = build()
    print(f"metadata.json: {path}")
    print()
    print("DuckDB sanity:")
    print(
        f'  duckdb -c "LOAD iceberg; SELECT COUNT(*) FROM iceberg_scan(\'{path}\');"'
    )
