"""V3 Iceberg with native `geometry` + row-lineage enabled.

Companion to `v3_geometry.py`. The two fixtures together demonstrate
both spec-permitted V3 variants:

  - `v3_geometry`         — spec-minimal: `row-lineage: false`. No
                            lineage columns in data files. Smaller,
                            cleaner. The recommended starting point
                            for V3 readers.
  - `v3_geometry_lineage` — `row-lineage: true`. Each data file
                            carries `_row_id` and
                            `_last_updated_sequence_number` columns
                            populated with monotonic values. Stricter
                            engines (Snowflake's V3 unmanaged reader,
                            and any reader that requires lineage
                            columns be present regardless of the
                            metadata flag) are expected to accept this
                            where they reject the minimal fixture.

Field IDs for the lineage columns are the Apache Iceberg V3 spec
values:
  _row_id                          → field id 2147483545
  _last_updated_sequence_number    → field id 2147483544

These are *metadata columns* — not part of the user schema in
`metadata.json`'s `schemas[].fields`. They appear only in the parquet
data files, with field IDs above 2^31 - 1 so they can't collide with
user field IDs. (Snowflake uses different field IDs and the names
`METADATA$RL_*` for these columns; we use the spec names + ids so this
fixture tests whether engines accept spec-conformant lineage rather
than Snowflake-specific lineage.)
"""

from __future__ import annotations

import random
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import geoarrow.pyarrow as ga
from pyiceberg.schema import Schema
from pyiceberg.types import BinaryType, NestedField, StringType

from .common import REGIONS, packed_xy_le, stable_seed, wkb_point_le
from ._static_catalog import write_static_catalog

ROOT = Path(__file__).parent.parent / "data" / "v3_geometry_lineage"

# Apache Iceberg V3 spec field IDs for row-lineage metadata columns.
# Source: org.apache.iceberg.MetadataColumns (Integer.MAX_VALUE - 102 / -103).
ROW_ID_FIELD_ID = 2147483545
LAST_UPDATED_SEQ_FIELD_ID = 2147483544


def _field_meta(field_id: int) -> dict:
    return {"PARQUET:field_id": str(field_id)}


PY_SCHEMA = Schema(
    NestedField(1, "id", StringType(), required=False),
    NestedField(2, "geom", BinaryType(), required=False),
)

GEOM_EXT_TYPE = ga.wkb().with_crs(ga.OGC_CRS84)

ARROW_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string(), nullable=True, metadata=_field_meta(1)),
        pa.field("geom", GEOM_EXT_TYPE, nullable=True, metadata=_field_meta(2)),
        pa.field("_row_id", pa.int64(), nullable=True,
                 metadata=_field_meta(ROW_ID_FIELD_ID)),
        pa.field("_last_updated_sequence_number", pa.int64(), nullable=True,
                 metadata=_field_meta(LAST_UPDATED_SEQ_FIELD_ID)),
    ]
)


def _write_parquet(region, row_id_offset: int, sequence_number: int) -> Path:
    rng = random.Random(stable_seed(region.name))
    rows = 1000
    ids, wkbs = [], []
    for i in range(rows):
        x = rng.uniform(region.xmin, region.xmax)
        y = rng.uniform(region.ymin, region.ymax)
        ids.append(f"{region.name}-{i}")
        wkbs.append(wkb_point_le(x, y))
    geom_arr = GEOM_EXT_TYPE.wrap_array(pa.array(wkbs, type=pa.binary()))
    row_ids = pa.array(range(row_id_offset, row_id_offset + rows), type=pa.int64())
    last_updated = pa.array([sequence_number] * rows, type=pa.int64())
    table = pa.table(
        {
            "id": pa.array(ids, type=pa.string()),
            "geom": geom_arr,
            "_row_id": row_ids,
            "_last_updated_sequence_number": last_updated,
        },
        schema=ARROW_SCHEMA,
    )
    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{region.name}.parquet"
    pq.write_table(table, out, compression="zstd",
                   store_schema=True, write_statistics=True)
    return out


def build(*, location_uri: str | None = None, meta_dir_name: str = "metadata") -> Path:
    """Build the V3 geometry + row-lineage fixture."""
    sequence_number = 1
    data_files = []
    for i, region in enumerate(REGIONS):
        row_id_offset = i * 1000
        p = _write_parquet(region, row_id_offset, sequence_number)
        data_files.append(
            {
                "path": f"data/{region.name}.parquet",
                "size": p.stat().st_size,
                "rows": 1000,
                "lower": {2: packed_xy_le(region.xmin, region.ymin)},
                "upper": {2: packed_xy_le(region.xmax, region.ymax)},
            }
        )

    # User-visible schema fields. The row-lineage columns are
    # *metadata* columns and don't appear here per spec — engines
    # discover them via the well-known V3 field IDs in the data files.
    schema_fields = [
        {"id": 1, "name": "id", "required": False, "type": "string"},
        {"id": 2, "name": "geom", "required": False, "type": "geometry"},
    ]

    # `last-column-id` must account for the row-lineage columns too so
    # they're considered "assigned" at metadata level. Without this,
    # readers using last-column-id to validate field ID space conflict
    # detection could see the lineage columns as unassigned and reject.
    return write_static_catalog(
        table_root=ROOT,
        iceberg_schema=PY_SCHEMA,
        schema_json_fields=schema_fields,
        name_mapping=[
            {"field-id": 1, "names": ["id"]},
            {"field-id": 2, "names": ["geom"]},
            {"field-id": ROW_ID_FIELD_ID, "names": ["_row_id"]},
            {"field-id": LAST_UPDATED_SEQ_FIELD_ID,
             "names": ["_last_updated_sequence_number"]},
        ],
        data_files=data_files,
        format_version_in_metadata=3,
        location_uri=location_uri,
        meta_dir_name=meta_dir_name,
        row_lineage=True,
    )


if __name__ == "__main__":
    path = build()
    print(f"metadata.json: {path}")
    print()
    print("Companion fixture to v3_geometry — same data + spatial bounds,")
    print("plus _row_id and _last_updated_sequence_number lineage columns")
    print("written into the parquet data files at spec field IDs.")
