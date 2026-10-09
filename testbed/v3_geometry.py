"""V3 Iceberg with a native `geometry` column.

The reference V3 geo fixture for this testbed. Goals:

  - **GeoParquet 2.0 parquet files.** The `geom` column is written with
    Parquet's native `Geometry` logical type (`BYTE_ARRAY` physical, WKB
    encoded), via the `geoarrow-pyarrow` extension — what a V3 reader
    expects — and each file carries the GeoParquet 2.0 `geo` footer
    metadata (version 2.0.0, per-file bbox) so it is a conformant
    GeoParquet 2.0 file on its own. Iceberg never reads that footer.

  - **Spec-minimal V3.** `format-version: 3` with `row-lineage: false`
    explicitly. We do NOT emit the V3 row-lineage metadata columns
    (`_row_id`, `_last_updated_sequence_number`) — the spec permits
    leaving them out when row lineage is off. Engines that require them
    regardless (e.g. Snowflake's V3 unmanaged reader appears to) are
    being stricter than the spec; we document that as the engine's
    behavior, not adapt to it.

  - **V3 manifest avro** with per-file geometry bounds in the
    `packed_xy_le` encoding (16 bytes: little-endian X, little-endian
    Y), `first_row_id` populated, and the V3-shape avro metadata
    Snowflake's own writer produces (verified by direct comparison).

This is intended to be the *reference catalog* that V3 readers test
against. If a reader rejects this fixture, that's a reader-side gap to
file, not a catalog-side bug to fix.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import geoarrow.pyarrow as ga
from pyiceberg.schema import Schema
from pyiceberg.types import GeometryType, NestedField, StringType

from .common import REGIONS, geoparquet_geo_metadata, packed_xy_le, stable_seed, wkb_point_le
from ._static_catalog import write_static_catalog

ROOT = Path(__file__).parent.parent / "data" / "v3_geometry"


def _field_meta(field_id: int) -> dict:
    return {"PARQUET:field_id": str(field_id)}


# pyiceberg >= 0.12.0 has a native GeometryType; it serializes as the bare
# "geometry" token, the same shape Snowflake's V3 writer emits. This python
# schema is only consumed by the manifest writer — the column type readers
# see is declared in the hand-written metadata.json and in the parquet file
# (native Geometry logical type via geoarrow-pyarrow).
PY_SCHEMA = Schema(
    NestedField(1, "id", StringType(), required=False),
    NestedField(2, "geom", GeometryType(), required=False),
)

# The geom field is written as a geoarrow.wkb extension array which
# serializes to Parquet's native `Geometry(crs=)` logical type.
GEOM_EXT_TYPE = ga.wkb().with_crs(ga.OGC_CRS84)

ARROW_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string(), nullable=True, metadata=_field_meta(1)),
        pa.field("geom", GEOM_EXT_TYPE, nullable=True, metadata=_field_meta(2)),
    ]
)


def _write_parquet(region) -> Path:
    rng = random.Random(stable_seed(region.name))
    rows = 1000
    ids, wkbs, xs, ys = [], [], [], []
    for i in range(rows):
        x = rng.uniform(region.xmin, region.xmax)
        y = rng.uniform(region.ymin, region.ymax)
        ids.append(f"{region.name}-{i}")
        wkbs.append(wkb_point_le(x, y))
        xs.append(x)
        ys.append(y)
    geom_arr = GEOM_EXT_TYPE.wrap_array(pa.array(wkbs, type=pa.binary()))
    table = pa.table({"id": pa.array(ids, type=pa.string()), "geom": geom_arr},
                     schema=ARROW_SCHEMA)
    # GeoParquet 2.0 `geo` footer (per-file bbox); Iceberg ignores it.
    table = table.replace_schema_metadata(
        {"geo": geoparquet_geo_metadata("geom", xs=xs, ys=ys)}
    )
    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{region.name}.parquet"
    pq.write_table(table, out, compression="zstd",
                   store_schema=True, write_statistics=True)
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
        # Populate per-file metrics on both columns. They're spec-optional
        # but required for some strict V3 readers — Snowflake's V3
        # manifest-bound pruner trips a variant-cast error on the
        # `packed_xy_le` geom bound when these are missing for the ID
        # column (`Failed to cast variant value "..." to REAL`), even
        # though it parses the table fine. Populating value_counts +
        # null_value_counts + lower/upper bounds for the ID column makes
        # the predicate path work too.
        first_id, last_id = f"{region.name}-0".encode(), f"{region.name}-999".encode()
        data_files.append(
            {
                "path": f"data/{region.name}.parquet",
                "size": p.stat().st_size,
                "rows": 1000,
                "lower": {1: first_id, 2: enc(region.xmin, region.ymin)},
                "upper": {1: last_id, 2: enc(region.xmax, region.ymax)},
                "value_counts": {1: 1000, 2: 1000},
                "null_value_counts": {1: 0, 2: 0},
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
        "DuckDB probe (>= 1.5.6: expected Total Files Read: 1 — manifest "
        "geometry-bound pruning fires on bbox predicates; a plain "
        "ST_Intersects returns the same rows but reads all 10 files):"
    )
    print(
        f"  duckdb -c \"LOAD iceberg; LOAD spatial; EXPLAIN ANALYZE SELECT COUNT(*) "
        f"FROM iceberg_scan('{path}') "
        f"WHERE ST_Intersects_Extent(geom, ST_MakeEnvelope(-125, 32, -115, 42));\""
    )
