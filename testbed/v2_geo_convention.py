"""V2 Iceberg laid out per the proposed "GeoIceberg V2" convention.

Tests three design decisions in one fixture:
  1. Free-form column names — bbox cols renamed to fp_xmin/fp_ymin/...,
     geometry payload column named geom_wkb. If file pruning still works,
     Iceberg's manifest pruning is name-agnostic (column-id based).
  2. A `geom_wkb BINARY` column carries the WKB payload alongside the
     bbox cols. Engines that compute geo predicates read it via
     ST_GeomFromWKB.
  3. A structured `geo` table property declares the per-column convention:
     CRS, edges, encoding, which cols are the bbox, multi-geom-ready
     shape mirroring GeoParquet 1.1's `geo` metadata.

The schema for this fixture is:
  id        STRING        (field id 1)
  fp_xmin   DOUBLE        (field id 2)   -- four file-prune cols, manifest
  fp_ymin   DOUBLE        (field id 3)      lower/upper populated
  fp_xmax   DOUBLE        (field id 4)
  fp_ymax   DOUBLE        (field id 5)
  geom_wkb  BINARY        (field id 6)   -- WKB payload (point per row)
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.schema import Schema
from pyiceberg.types import BinaryType, DoubleType, NestedField, StringType

from .common import REGIONS, double_le, stable_seed, wkb_point_le
from ._static_catalog import write_static_catalog

ROOT = Path(__file__).parent.parent / "data" / "v2_geo_convention"


def _field_meta(field_id: int) -> dict:
    return {"PARQUET:field_id": str(field_id)}


ARROW_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string(), nullable=True, metadata=_field_meta(1)),
        pa.field("fp_xmin", pa.float64(), nullable=True, metadata=_field_meta(2)),
        pa.field("fp_ymin", pa.float64(), nullable=True, metadata=_field_meta(3)),
        pa.field("fp_xmax", pa.float64(), nullable=True, metadata=_field_meta(4)),
        pa.field("fp_ymax", pa.float64(), nullable=True, metadata=_field_meta(5)),
        pa.field("geom_wkb", pa.binary(), nullable=True, metadata=_field_meta(6)),
    ]
)

ICEBERG_SCHEMA = Schema(
    NestedField(1, "id", StringType(), required=False),
    NestedField(2, "fp_xmin", DoubleType(), required=False),
    NestedField(3, "fp_ymin", DoubleType(), required=False),
    NestedField(4, "fp_xmax", DoubleType(), required=False),
    NestedField(5, "fp_ymax", DoubleType(), required=False),
    NestedField(6, "geom_wkb", BinaryType(), required=False),
)

# The convention's table-property payload. Mirrors GeoParquet 1.1's `geo`
# metadata block in shape, so the mental model carries over directly.
GEO_PROPERTY = {
    "version": "1.0",
    "primary_column": "geom_wkb",
    "columns": {
        "geom_wkb": {
            "encoding": "WKB",
            "crs": "OGC:CRS84",
            "edges": "planar",
            "bbox_columns": ["fp_xmin", "fp_ymin", "fp_xmax", "fp_ymax"],
        }
    },
}


def _write_parquet(region) -> Path:
    rng = random.Random(stable_seed(region.name))
    rows = 1000
    ids, xmins, ymins, xmaxs, ymaxs, wkbs = [], [], [], [], [], []
    for i in range(rows):
        x = rng.uniform(region.xmin, region.xmax)
        y = rng.uniform(region.ymin, region.ymax)
        ids.append(f"{region.name}-{i}")
        # Each row is a 0.001×0.001 box around the point. The bbox cols
        # bound the box, the WKB payload is the point itself.
        xmins.append(x)
        ymins.append(y)
        xmaxs.append(x + 0.001)
        ymaxs.append(y + 0.001)
        wkbs.append(wkb_point_le(x, y))
    table = pa.table(
        {
            "id": pa.array(ids, type=pa.string()),
            "fp_xmin": pa.array(xmins, type=pa.float64()),
            "fp_ymin": pa.array(ymins, type=pa.float64()),
            "fp_xmax": pa.array(xmaxs, type=pa.float64()),
            "fp_ymax": pa.array(ymaxs, type=pa.float64()),
            "geom_wkb": pa.array(wkbs, type=pa.binary()),
        },
        schema=ARROW_SCHEMA,
    )
    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{region.name}.parquet"
    pq.write_table(table, out, compression="zstd")
    return out


def build(*, location_uri: str | None = None, meta_dir_name: str = "metadata") -> Path:
    data_files = []
    for region in REGIONS:
        p = _write_parquet(region)
        data_files.append(
            {
                "path": f"data/{region.name}.parquet",
                "size": p.stat().st_size,
                "rows": 1000,
                # Per-file bounds for the bbox columns. Standard
                # little-endian-double encoding. We deliberately do NOT
                # write bounds for the BINARY geom_wkb column — BLOB
                # min/max isn't useful for spatial pruning.
                "lower": {
                    2: double_le(region.xmin),
                    3: double_le(region.ymin),
                    4: double_le(region.xmin + 0.001),
                    5: double_le(region.ymin + 0.001),
                },
                "upper": {
                    2: double_le(region.xmax),
                    3: double_le(region.ymax),
                    4: double_le(region.xmax + 0.001),
                    5: double_le(region.ymax + 0.001),
                },
            }
        )

    return write_static_catalog(
        table_root=ROOT,
        iceberg_schema=ICEBERG_SCHEMA,
        schema_json_fields=[
            {"id": 1, "name": "id", "required": False, "type": "string"},
            {"id": 2, "name": "fp_xmin", "required": False, "type": "double"},
            {"id": 3, "name": "fp_ymin", "required": False, "type": "double"},
            {"id": 4, "name": "fp_xmax", "required": False, "type": "double"},
            {"id": 5, "name": "fp_ymax", "required": False, "type": "double"},
            {"id": 6, "name": "geom_wkb", "required": False, "type": "binary"},
        ],
        name_mapping=[
            {"field-id": 1, "names": ["id"]},
            {"field-id": 2, "names": ["fp_xmin"]},
            {"field-id": 3, "names": ["fp_ymin"]},
            {"field-id": 4, "names": ["fp_xmax"]},
            {"field-id": 5, "names": ["fp_ymax"]},
            {"field-id": 6, "names": ["geom_wkb"]},
        ],
        data_files=data_files,
        extra_properties={"geo": json.dumps(GEO_PROPERTY)},
        location_uri=location_uri,
        meta_dir_name=meta_dir_name,
    )


if __name__ == "__main__":
    path = build()
    print(f"metadata.json: {path}")
    print()
    print("DuckDB probes:")
    print()
    print("# Q1 — bbox-predicate (file pruning expected; L3):")
    print(
        f"  duckdb -c \"LOAD iceberg; EXPLAIN ANALYZE SELECT COUNT(*) "
        f"FROM iceberg_scan('{path}') "
        f"WHERE fp_xmin <= -118 AND fp_xmax >= -125 "
        f"AND fp_ymin <= 40 AND fp_ymax >= 37;\""
    )
    print()
    print("# Q2 — materialize WKB geometries (end-to-end read):")
    print(
        f"  duckdb -c \"LOAD iceberg; LOAD spatial; SELECT id, "
        f"ST_AsText(ST_GeomFromWKB(geom_wkb)) "
        f"FROM iceberg_scan('{path}') LIMIT 5;\""
    )
    print()
    print("# Q3 — ST_Intersects-only (no bbox-cols predicate; "
          "tests whether engine derives bbox automatically):")
    print(
        f"  duckdb -c \"LOAD iceberg; LOAD spatial; EXPLAIN ANALYZE SELECT COUNT(*) "
        f"FROM iceberg_scan('{path}') "
        f"WHERE ST_Intersects(ST_GeomFromWKB(geom_wkb), "
        f"ST_MakeEnvelope(-125, 32, -115, 42));\""
    )
