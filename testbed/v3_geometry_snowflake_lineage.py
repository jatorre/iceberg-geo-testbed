"""V3 Iceberg geometry fixture that exactly mimics Snowflake's own managed V3 shape.

Sibling to `v3_geometry.py` (spec-minimal, no lineage columns) and
`v3_geometry_lineage.py` (spec-conformant lineage at spec field IDs,
populated values). Both of those get rejected by Snowflake's V3
*unmanaged* reader with "incomplete state". This fixture is the
empirical answer to "what does Snowflake actually look for?"

By inspecting a Snowflake-managed V3 parquet file directly:

  - Two physical metadata columns are present, with Snowflake-internal
    names and field IDs (not the Iceberg-spec ones):

        METADATA$RL_ROW_ID                            field_id 2147483540  int64
        METADATA$RL_LAST_UPDATED_SEQUENCE_NUMBER      field_id 2147483539  int64

  - Their values are entirely NULL (yes, even though they exist).
  - The metadata.json does **not** declare `row-lineage` at all (the
    key is omitted — not `false`, not `true`).
  - The parquet column order is GEOM first, then ID (then the two
    lineage columns).

This fixture matches all of that. If Snowflake's unmanaged V3 reader
accepts it, the gap was purely a writer-side issue (we weren't
mimicking Snowflake's exact shape) — not a Snowflake-side limitation.
That changes how we talk about Snowflake's V3 support in the matrix.
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

ROOT = Path(__file__).parent.parent / "data" / "v3_geometry_snowflake_lineage"

# Snowflake's V3 row-lineage columns. These are NOT the Iceberg-spec
# field IDs (which would be 2147483545 / 2147483544 with names
# `_row_id` / `_last_updated_sequence_number`); these are what
# Snowflake's own managed V3 writer emits — its reader apparently
# requires precisely these.
SF_ROW_ID_FIELD_ID = 2147483540
SF_LAST_UPDATED_SEQ_FIELD_ID = 2147483539
SF_ROW_ID_NAME = "METADATA$RL_ROW_ID"
SF_LAST_UPDATED_SEQ_NAME = "METADATA$RL_LAST_UPDATED_SEQUENCE_NUMBER"


def _field_meta(field_id: int) -> dict:
    return {"PARQUET:field_id": str(field_id)}


# Iceberg schema for the user-visible columns. The lineage columns are
# metadata-only and don't appear here (same convention as
# v3_geometry_lineage.py).
PY_SCHEMA = Schema(
    NestedField(1, "ID", StringType(), required=False),
    NestedField(2, "GEOM", BinaryType(), required=False),
)

GEOM_EXT_TYPE = ga.wkb().with_crs(ga.OGC_CRS84)

# Column order + casing match Snowflake's parquet exactly: GEOM, ID, then
# lineage cols. Snowflake case-folds unquoted identifiers to upper, so its
# own writer emits these as uppercase; we mirror that.
ARROW_SCHEMA = pa.schema(
    [
        pa.field("GEOM", GEOM_EXT_TYPE, nullable=True, metadata=_field_meta(2)),
        pa.field("ID", pa.string(), nullable=True, metadata=_field_meta(1)),
        pa.field(SF_ROW_ID_NAME, pa.int64(), nullable=True,
                 metadata=_field_meta(SF_ROW_ID_FIELD_ID)),
        pa.field(SF_LAST_UPDATED_SEQ_NAME, pa.int64(), nullable=True,
                 metadata=_field_meta(SF_LAST_UPDATED_SEQ_FIELD_ID)),
    ]
)


def _write_parquet(region) -> Path:
    rng = random.Random(stable_seed(region.name))
    rows = 1000
    ids, wkbs = [], []
    for i in range(rows):
        x = rng.uniform(region.xmin, region.xmax)
        y = rng.uniform(region.ymin, region.ymax)
        ids.append(f"{region.name}-{i}")
        wkbs.append(wkb_point_le(x, y))
    geom_arr = GEOM_EXT_TYPE.wrap_array(pa.array(wkbs, type=pa.binary()))
    nulls = pa.array([None] * rows, type=pa.int64())
    table = pa.table(
        {
            "GEOM": geom_arr,
            "ID": pa.array(ids, type=pa.string()),
            SF_ROW_ID_NAME: nulls,
            SF_LAST_UPDATED_SEQ_NAME: nulls,
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
    """Build the Snowflake-shaped V3 geometry fixture."""
    data_files = []
    for region in REGIONS:
        p = _write_parquet(region)
        # Mirror what Snowflake-managed V3 writes in the manifest:
        # value_counts/null_value_counts for both user columns + lower/upper
        # bounds for the string ID column (in addition to the geometry
        # bound). The lineage columns are all-null and excluded from
        # bounds — matches Snowflake.
        first_id = f"{region.name}-0".encode()
        last_id = f"{region.name}-999".encode()
        data_files.append(
            {
                "path": f"data/{region.name}.parquet",
                "size": p.stat().st_size,
                "rows": 1000,
                "lower": {1: first_id, 2: packed_xy_le(region.xmin, region.ymin)},
                "upper": {1: last_id, 2: packed_xy_le(region.xmax, region.ymax)},
                "value_counts": {1: 1000, 2: 1000},
                "null_value_counts": {1: 0, 2: 0},
            }
        )

    schema_fields = [
        {"id": 1, "name": "ID", "required": False, "type": "string"},
        {"id": 2, "name": "GEOM", "required": False, "type": "geometry"},
    ]
    return write_static_catalog(
        table_root=ROOT,
        iceberg_schema=PY_SCHEMA,
        schema_json_fields=schema_fields,
        name_mapping=[
            {"field-id": 1, "names": ["ID"]},
            {"field-id": 2, "names": ["GEOM"]},
            {"field-id": SF_ROW_ID_FIELD_ID, "names": [SF_ROW_ID_NAME]},
            {"field-id": SF_LAST_UPDATED_SEQ_FIELD_ID,
             "names": [SF_LAST_UPDATED_SEQ_NAME]},
        ],
        data_files=data_files,
        format_version_in_metadata=3,
        location_uri=location_uri,
        meta_dir_name=meta_dir_name,
        # Snowflake's managed V3 metadata.json doesn't have this key
        # at all — pass None to omit it rather than emit `false`.
        row_lineage=None,
        # Snowflake's metadata.json reports last-column-id=4 for a 2-user-
        # column V3 table, apparently reserving slots for the row-lineage
        # metadata cols. Match that exactly.
        last_column_id_override=4,
    )


if __name__ == "__main__":
    path = build()
    print(f"metadata.json: {path}")
    print()
    print("Snowflake-shaped V3 fixture — parquet schema mirrors what")
    print("Snowflake's managed V3 writer emits:")
    print(f"  GEOM, ID, {SF_ROW_ID_NAME}, {SF_LAST_UPDATED_SEQ_NAME}")
    print("with the lineage columns at Snowflake-internal field IDs")
    print(f"({SF_ROW_ID_FIELD_ID} / {SF_LAST_UPDATED_SEQ_FIELD_ID}) and")
    print("filled with NULL values. metadata.json omits `row-lineage`.")
