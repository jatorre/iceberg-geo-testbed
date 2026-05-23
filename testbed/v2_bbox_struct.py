"""V2 Iceberg with the GeoParquet-1.1-style `bbox` STRUCT column.

This is the "natural" GeoParquet covering convention. Expected DuckDB 1.5.3
result: pruning does NOT happen — predicates on struct fields don't push to
manifest bounds.
  Total Files Read: 10
"""

from __future__ import annotations

import random
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.schema import Schema
from pyiceberg.types import DoubleType, NestedField, StringType, StructType

from .common import REGIONS, double_le, stable_seed
from ._static_catalog import write_static_catalog

ROOT = Path(__file__).parent.parent / "data" / "v2_bbox_struct"


def _field_meta(field_id: int) -> dict:
    return {"PARQUET:field_id": str(field_id)}


ARROW_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string(), nullable=True, metadata=_field_meta(1)),
        pa.field(
            "bbox",
            pa.struct(
                [
                    pa.field("xmin", pa.float64(), nullable=True, metadata=_field_meta(5)),
                    pa.field("ymin", pa.float64(), nullable=True, metadata=_field_meta(6)),
                    pa.field("xmax", pa.float64(), nullable=True, metadata=_field_meta(7)),
                    pa.field("ymax", pa.float64(), nullable=True, metadata=_field_meta(8)),
                ]
            ),
            nullable=True,
            metadata=_field_meta(2),
        ),
    ]
)

ICEBERG_SCHEMA = Schema(
    NestedField(1, "id", StringType(), required=False),
    NestedField(
        2,
        "bbox",
        StructType(
            NestedField(5, "xmin", DoubleType(), required=False),
            NestedField(6, "ymin", DoubleType(), required=False),
            NestedField(7, "xmax", DoubleType(), required=False),
            NestedField(8, "ymax", DoubleType(), required=False),
        ),
        required=False,
    ),
)


def _write_parquet(region) -> Path:
    rng = random.Random(stable_seed(region.name))
    rows = 1000
    xmins, ymins, xmaxs, ymaxs, ids = [], [], [], [], []
    for i in range(rows):
        x0 = rng.uniform(region.xmin, region.xmax)
        y0 = rng.uniform(region.ymin, region.ymax)
        xmins.append(x0)
        ymins.append(y0)
        xmaxs.append(x0 + 0.001)
        ymaxs.append(y0 + 0.001)
        ids.append(f"{region.name}-{i}")
    bbox_struct = pa.StructArray.from_arrays(
        [
            pa.array(xmins, type=pa.float64()),
            pa.array(ymins, type=pa.float64()),
            pa.array(xmaxs, type=pa.float64()),
            pa.array(ymaxs, type=pa.float64()),
        ],
        fields=ARROW_SCHEMA.field("bbox").type,
    )
    table = pa.table(
        {
            "id": pa.array(ids, type=pa.string()),
            "bbox": bbox_struct,
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
                "lower": {
                    5: double_le(region.xmin),
                    6: double_le(region.ymin),
                    7: double_le(region.xmin + 0.001),
                    8: double_le(region.ymin + 0.001),
                },
                "upper": {
                    5: double_le(region.xmax),
                    6: double_le(region.ymax),
                    7: double_le(region.xmax + 0.001),
                    8: double_le(region.ymax + 0.001),
                },
            }
        )

    return write_static_catalog(
        table_root=ROOT,
        iceberg_schema=ICEBERG_SCHEMA,
        schema_json_fields=[
            {"id": 1, "name": "id", "required": False, "type": "string"},
            {
                "id": 2,
                "name": "bbox",
                "required": False,
                "type": {
                    "type": "struct",
                    "fields": [
                        {"id": 5, "name": "xmin", "required": False, "type": "double"},
                        {"id": 6, "name": "ymin", "required": False, "type": "double"},
                        {"id": 7, "name": "xmax", "required": False, "type": "double"},
                        {"id": 8, "name": "ymax", "required": False, "type": "double"},
                    ],
                },
            },
        ],
        name_mapping=[
            {"field-id": 1, "names": ["id"]},
            {
                "field-id": 2,
                "names": ["bbox"],
                "fields": [
                    {"field-id": 5, "names": ["xmin"]},
                    {"field-id": 6, "names": ["ymin"]},
                    {"field-id": 7, "names": ["xmax"]},
                    {"field-id": 8, "names": ["ymax"]},
                ],
            },
        ],
        data_files=data_files,
        location_uri=location_uri,
        meta_dir_name=meta_dir_name,
    )


if __name__ == "__main__":
    path = build()
    print(f"metadata.json: {path}")
    print()
    print("DuckDB probe (expect Total Files Read: 10 — struct-pushdown gap):")
    print(
        f"  duckdb -c \"LOAD iceberg; EXPLAIN ANALYZE SELECT COUNT(*) "
        f"FROM iceberg_scan('{path}') "
        f"WHERE bbox.xmin <= -118 AND bbox.xmax >= -125 AND bbox.ymin <= 40 AND bbox.ymax >= 37;\""
    )
