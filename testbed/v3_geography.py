"""V3 Iceberg with a native `geography` column.

The geography twin of `v3_geometry`. Same 10 regions, same seeds, same
points — the *only* difference is the declared type:

  - parquet logical type `Geography(crs=, algorithm=spherical)` instead of
    `Geometry(crs=)` (parquet-format 2.11 GEOGRAPHY vs GEOMETRY), written
    via `geoarrow-pyarrow`'s spherical edge type.
  - Iceberg type token `"geography"` instead of `"geometry"` in
    metadata.json.

Because the coordinates are byte-identical to `v3_geometry`, the pair is a
controlled A/B: if a reader handles one and not the other, the delta is the
type annotation alone, not the data. That's the isolation a reader team
wants when triaging 2.11 logical-type support.

Type token: bare `"geography"`, matching the bare `"geometry"` choice in
`v3_geometry` (see that module's note). Per the Iceberg V3 spec the
defaults for the omitted parameters are `OGC:CRS84` and `spherical` — the
same values we write into the parquet annotation, so the two layers agree.

Bounds are `packed_xy_le` (16 bytes: LE X, LE Y), the same V3 manifest
encoding geometry uses.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import geoarrow.pyarrow as ga
from pyiceberg.schema import Schema
from pyiceberg.types import GeographyType, NestedField, StringType

from .common import REGIONS, geoparquet_geo_metadata, packed_xy_le, stable_seed, wkb_point_le
from ._static_catalog import write_static_catalog

ROOT = Path(__file__).parent.parent / "data" / "v3_geography"


def _field_meta(field_id: int) -> dict:
    return {"PARQUET:field_id": str(field_id)}


# pyiceberg >= 0.12.0 has a native GeographyType (bare "geography" token;
# CRS / algorithm default to OGC:CRS84 / spherical per spec). As in
# v3_geometry, the type readers see lives in metadata.json + the parquet
# annotation; this schema only feeds the manifest writer.
PY_SCHEMA = Schema(
    NestedField(1, "id", StringType(), required=False),
    NestedField(2, "geog", GeographyType(), required=False),
)

# Spherical edges are what make this GEOGRAPHY rather than GEOMETRY: pyarrow
# maps the geoarrow spherical edge type onto parquet's Geography logical type
# with `algorithm=spherical`.
GEOG_EXT_TYPE = ga.wkb().with_crs(ga.OGC_CRS84).with_edge_type("spherical")

ARROW_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string(), nullable=True, metadata=_field_meta(1)),
        pa.field("geog", GEOG_EXT_TYPE, nullable=True, metadata=_field_meta(2)),
    ]
)


def _write_parquet(region) -> Path:
    # Same seed as v3_geometry so the coordinates match file-for-file.
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
    geog_arr = GEOG_EXT_TYPE.wrap_array(pa.array(wkbs, type=pa.binary()))
    table = pa.table({"id": pa.array(ids, type=pa.string()), "geog": geog_arr},
                     schema=ARROW_SCHEMA)
    # GeoParquet 2.0 `geo` footer (per-file bbox); Iceberg ignores it.
    table = table.replace_schema_metadata(
        {"geo": geoparquet_geo_metadata("geog", xs=xs, ys=ys, edges="spherical")}
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
    """encoding ∈ {"packed_xy", "wkb_point"} — the encoding used for the geography
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
        # Full per-file metrics on both columns, for the same reason as
        # v3_geometry: spec-optional, but strict readers need them.
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
            {"id": 2, "name": "geog", "required": False, "type": "geography"},
        ],
        name_mapping=[
            {"field-id": 1, "names": ["id"]},
            {"field-id": 2, "names": ["geog"]},
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
        help="Geography bound byte encoding to write into the manifest.",
    )
    args = ap.parse_args()
    path = build(args.encoding)
    print(f"metadata.json: {path}")
    print(f"bound encoding: {args.encoding}")
