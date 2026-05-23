"""V2 Iceberg table with flat xmin/ymin/xmax/ymax double columns.

This is the working baseline today: DuckDB 1.5.3 prunes files at manifest
level for top-level numeric columns. Expected EXPLAIN ANALYZE result:
  Total Files Read: 1
"""

from __future__ import annotations

import random
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.schema import Schema
from pyiceberg.types import DoubleType, NestedField, StringType

from .common import REGIONS, double_le, stable_seed
from ._static_catalog import write_static_catalog

ROOT = Path(__file__).parent.parent / "data" / "v2_flat_columns"


def _field_meta(field_id: int) -> dict:
    return {"PARQUET:field_id": str(field_id)}


ARROW_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string(), nullable=True, metadata=_field_meta(1)),
        pa.field("xmin", pa.float64(), nullable=True, metadata=_field_meta(2)),
        pa.field("ymin", pa.float64(), nullable=True, metadata=_field_meta(3)),
        pa.field("xmax", pa.float64(), nullable=True, metadata=_field_meta(4)),
        pa.field("ymax", pa.float64(), nullable=True, metadata=_field_meta(5)),
    ]
)

ICEBERG_SCHEMA = Schema(
    NestedField(1, "id", StringType(), required=False),
    NestedField(2, "xmin", DoubleType(), required=False),
    NestedField(3, "ymin", DoubleType(), required=False),
    NestedField(4, "xmax", DoubleType(), required=False),
    NestedField(5, "ymax", DoubleType(), required=False),
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
    table = pa.table(
        {
            "id": pa.array(ids, type=pa.string()),
            "xmin": pa.array(xmins, type=pa.float64()),
            "ymin": pa.array(ymins, type=pa.float64()),
            "xmax": pa.array(xmaxs, type=pa.float64()),
            "ymax": pa.array(ymaxs, type=pa.float64()),
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
                # Per-file bounds for the four flat double columns. lower_bound
                # of a column = min over rows in that file; upper_bound = max.
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
            {"id": 2, "name": "xmin", "required": False, "type": "double"},
            {"id": 3, "name": "ymin", "required": False, "type": "double"},
            {"id": 4, "name": "xmax", "required": False, "type": "double"},
            {"id": 5, "name": "ymax", "required": False, "type": "double"},
        ],
        name_mapping=[
            {"field-id": 1, "names": ["id"]},
            {"field-id": 2, "names": ["xmin"]},
            {"field-id": 3, "names": ["ymin"]},
            {"field-id": 4, "names": ["xmax"]},
            {"field-id": 5, "names": ["ymax"]},
        ],
        data_files=data_files,
        location_uri=location_uri,
        meta_dir_name=meta_dir_name,
    )


if __name__ == "__main__":
    path = build()
    print(f"metadata.json: {path}")
    print()
    print("DuckDB probe (California bbox; expect Total Files Read: 1):")
    print(
        f"  duckdb -c \"LOAD iceberg; EXPLAIN ANALYZE SELECT COUNT(*) "
        f"FROM iceberg_scan('{path}') "
        f"WHERE xmin <= -118 AND xmax >= -125 AND ymin <= 40 AND ymax >= 37;\""
    )
