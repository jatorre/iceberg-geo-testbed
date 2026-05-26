"""V3 Iceberg with a native `geometry(OGC:CRS84)` column.

The metadata.json declares format-version: 3 and the column type as
`geometry(OGC:CRS84)`. The manifest carries per-file lower_bound / upper_bound
for the geometry column. We try multiple bound encodings because there's no
canonical "this is what DuckDB expects" today.

Expected DuckDB 1.5.3 result (May 2026): the bound deserialization step in
`IcebergValue::DeserializeValue` has no GEOMETRY case, so it bails with
  Invalid Configuration Error: Column geom lower bound deserialization failed:
  Failed to deserialize blob ... attempting to produce value of type
  'GEOMETRY(\\'OGC:CRS84\\')'

This file exists to make the failure reproducible and to be the asserting
test that flips to GREEN when DuckDB ships the GEOMETRY branch.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.schema import Schema
from pyiceberg.types import BinaryType, NestedField, StringType

from .common import REGIONS, packed_xy_le, stable_seed, wkb_point_le
from ._static_catalog import write_static_catalog

ROOT = Path(__file__).parent.parent / "data" / "v3_geometry"


def _field_meta(field_id: int) -> dict:
    return {"PARQUET:field_id": str(field_id)}


# pyiceberg 0.11.1 has no GeometryType — fall back to BinaryType in the python
# schema (used only by the manifest writer to validate field types). The actual
# column type is declared in the hand-written metadata.json below.
PY_SCHEMA = Schema(
    NestedField(1, "id", StringType(), required=False),
    NestedField(2, "geom", BinaryType(), required=False),
)

ARROW_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string(), nullable=True, metadata=_field_meta(1)),
        pa.field("geom", pa.binary(), nullable=True, metadata=_field_meta(2)),
    ]
)


def _write_parquet(region) -> Path:
    rng = random.Random(stable_seed(region.name))
    rows = 1000
    ids, geoms = [], []
    for i in range(rows):
        x = rng.uniform(region.xmin, region.xmax)
        y = rng.uniform(region.ymin, region.ymax)
        ids.append(f"{region.name}-{i}")
        geoms.append(wkb_point_le(x, y))
    table = pa.table(
        {
            "id": pa.array(ids, type=pa.string()),
            "geom": pa.array(geoms, type=pa.binary()),
        },
        schema=ARROW_SCHEMA,
    )
    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{region.name}.parquet"
    pq.write_table(table, out, compression="zstd")
    return out


def build(
    encoding: str = "packed_xy",
    *,
    location_uri: str | None = None,
    meta_dir_name: str = "metadata",
) -> Path:
    """encoding ∈ {"packed_xy", "wkb_point"} — the encoding used for the geometry
    column's lower/upper bound bytes."""
    if encoding == "packed_xy":
        def enc(x, y): return packed_xy_le(x, y)
    elif encoding == "wkb_point":
        def enc(x, y): return wkb_point_le(x, y)
    else:
        raise ValueError(f"unknown encoding: {encoding!r}")

    data_files = []
    for region in REGIONS:
        p = _write_parquet(region)
        data_files.append(
            {
                "path": f"data/{region.name}.parquet",
                "size": p.stat().st_size,
                "rows": 1000,
                "lower": {2: enc(region.xmin, region.ymin)},
                "upper": {2: enc(region.xmax, region.ymax)},
            }
        )

    return write_static_catalog(
        table_root=ROOT,
        iceberg_schema=PY_SCHEMA,
        schema_json_fields=[
            {"id": 1, "name": "id", "required": False, "type": "string"},
            # V3 native geometry type. Empirically verified: a
            # Snowflake-managed V3 GEOMETRY table writes the type as
            # bare `"geometry"` (no CRS in the type token) — CRS
            # tracking happens elsewhere (column properties / parquet
            # schema annotation). The earlier `"geometry(OGC:CRS84)"`
            # form (which DuckDB does parse) is *not* what Snowflake's
            # V3 writer emits; using the bare form keeps us
            # spec-compliant by example.
            {"id": 2, "name": "geom", "required": False, "type": "geometry"},
        ],
        name_mapping=[
            {"field-id": 1, "names": ["id"]},
            {"field-id": 2, "names": ["geom"]},
        ],
        data_files=data_files,
        format_version_in_metadata=3,
        location_uri=location_uri,
        meta_dir_name=meta_dir_name,
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--encoding",
        choices=["packed_xy", "wkb_point"],
        default="packed_xy",
        help="Geometry bound byte encoding to write into the manifest.",
    )
    args = ap.parse_args()
    path = build(args.encoding)
    print(f"metadata.json: {path}")
    print(f"bound encoding: {args.encoding}")
    print()
    print(
        "DuckDB probe (expected: bound deserialization failure today; "
        "Total Files Read: 1 once DuckDB lands the GEOMETRY branch):"
    )
    print(
        f"  duckdb -c \"LOAD iceberg; LOAD spatial; EXPLAIN ANALYZE SELECT COUNT(*) "
        f"FROM iceberg_scan('{path}') "
        f"WHERE ST_Intersects(geom, ST_MakeEnvelope(-125, 32, -115, 42));\""
    )
