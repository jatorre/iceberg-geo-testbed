# GeoIceberg V2 — recommended conventions

**Version 0.1** · 2026-05-26 · *Status: proposal*

This document describes a set of recommended conventions for storing
geospatial data in Apache Iceberg **V2** tables, so that engines today
deliver file-level pruning on spatial queries without waiting for the
ecosystem to implement Iceberg V3 geometry types.

It is deliberately modelled on **GeoParquet 1.1** — the equivalent
convention for the Parquet layer — and is designed to **compose** with
it. The two layers are independent and can be adopted incrementally;
adopting both gives row-group-level pruning inside files plus
file-level pruning across files.

When Iceberg V3 geometry types are widely supported, tables using this
convention can migrate in-place via `ALTER TABLE ADD COLUMN` — see §7.

The key words **MUST**, **SHOULD**, **MAY** are used per
[RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).

---

## 1 · Motivation

Iceberg V3 (mid-2025) introduced native `geometry`/`geography` types
with per-file bounds in the manifest, promising file-level pruning for
spatial predicates. As of mid-2026 this support is **not yet usable
end-to-end across any engine** we have tested:

- **DuckDB 1.5.3**: parses the type token; `IcebergValue::DeserializeValue`
  has no `GEOMETRY` branch (manifest-bound deserialization fails on the
  first spatial predicate); `BLOB → GEOMETRY` cast missing on `SELECT geom`.
- **BigQuery / BigLake**: `Unknown Iceberg type "geometry(OGC:CRS84)"`
  rejection at `CREATE EXTERNAL TABLE`.
- **Sedona / Iceberg-Spark 1.7.1**: `Cannot parse type string to primitive`
  rejection at metadata parse; the official Iceberg-Spark writer also
  cannot **write** V3 geometry (UDT mapper missing).
- **Databricks DBSQL 2026.10**: `[UNSUPPORTED_DATATYPE]` at parser level
  for both `GEOMETRY` and `GEOGRAPHY`.
- **Snowflake**: claims V3 geometry support in public preview but
  blocked by an account-side bug for our testing.
- **Oracle ADB 26ai**: rejects pyiceberg-emitted manifests at parser
  layer, separate concern.

The historical parallel is exactly **GeoParquet 1.1**: it emerged
because Parquet didn't yet have native geometry types, defining a
*convention layered on top* (a covering `bbox` struct + a WKB column +
a `geo` metadata block) so engines could deliver spatial pruning by
leveraging existing column-stats infrastructure. When Parquet later
gained native geometry types (→ GeoParquet 2.0), the convention was
adopted as the migration target.

**This document proposes the same pattern for Iceberg V2.**

---

## 2 · How it composes with GeoParquet

The two conventions operate at different layers and **MUST** be
treated as independent:

```
┌──────────────────────────────────────────────────────────────┐
│  GeoIceberg V2  (this spec)                                  │
│    • Table-level: bbox columns + geom_wkb column +           │
│      `geo` table property                                    │
│    • Engine reads Iceberg manifest lower/upper bounds on the │
│      bbox columns → prunes FILES whose bbox doesn't overlap  │
│      the query envelope                                      │
└──────────────────────────────────────────────────────────────┘
                              │
                              ↓  (within each surviving file)
┌──────────────────────────────────────────────────────────────┐
│  GeoParquet 1.1  (or 2.0 once native)                        │
│    • Per-row-group bbox struct → row-group level pruning     │
│    • Per-file `geo` metadata in parquet footer               │
└──────────────────────────────────────────────────────────────┘
```

A writer following GeoIceberg V2 **SHOULD** also follow GeoParquet 1.1
for the underlying parquet files. A reader implementing GeoIceberg V2
file pruning does not need to understand GeoParquet 1.1 — pruning at
each layer is independent.

---

## 3 · Schema requirements

A GeoIceberg V2 table **MUST** include, for each geometry it exposes:

| Column role          | Type      | Notes                                  |
|----------------------|-----------|----------------------------------------|
| WKB geometry payload | `binary`  | Well-Known Binary encoding             |
| bbox xmin            | `double`  | Min X (longitude/easting) for the row  |
| bbox ymin            | `double`  | Min Y (latitude/northing) for the row  |
| bbox xmax            | `double`  | Max X for the row                      |
| bbox ymax            | `double`  | Max Y for the row                      |

Column names are **NOT** prescribed — they are free-form and are
discovered via the table property described in §4. For tables with a
single geometry, the recommended default names are `geom_wkb` and
`xmin`/`ymin`/`xmax`/`ymax`, but any names that don't collide with
other table columns are permitted.

A writer **MUST** populate the four bbox columns with the literal
bounding rectangle of the geometry on each row, in the same CRS as the
geometry payload. A writer **MUST NOT** populate the bbox columns with
null values when the geometry is non-null.

A writer **MUST** ensure the Iceberg manifest records per-file
`lower_bound` / `upper_bound` for the four bbox columns. (Standard
Iceberg writers do this automatically for `double` columns; no extra
work is needed.) A writer **SHOULD NOT** record manifest bounds for
the WKB binary column — `BLOB` min/max is not meaningful for spatial
pruning.

Additional optional columns (`x_centroid`, `y_centroid`, geohash, H3,
etc.) **MAY** be added for sort-key / clustering purposes; they are
outside the scope of this convention.

---

## 4 · The `geo` table property

A GeoIceberg V2 table **MUST** set an Iceberg table property named
`geo`, whose value is a JSON-encoded object with the following shape
(mirroring GeoParquet 1.1's `geo` metadata block):

```json
{
  "version": "1.0",
  "primary_column": "geom_wkb",
  "columns": {
    "geom_wkb": {
      "encoding": "WKB",
      "crs": "OGC:CRS84",
      "edges": "planar",
      "bbox_columns": ["xmin", "ymin", "xmax", "ymax"]
    }
  }
}
```

Fields:

- `version` — convention version string. This document is `1.0`.
- `primary_column` — name of the geometry column that engines should
  use when only one is needed. **MUST** be a key in `columns`.
- `columns` — map keyed by geometry column name (one entry per
  geometry column on the table). Each entry has:
  - `encoding` — geometry encoding. **MUST** be `"WKB"` for this
    version of the spec. Future versions may add `"WKT"`, `"EWKB"`,
    or native types.
  - `crs` — CRS specification. **MUST** be one of:
    - `"OGC:CRS84"` for unprojected longitude/latitude (WGS84) with X-Y axis order
    - `"EPSG:nnnn"` for any EPSG-registered CRS
    - A JSON-encoded [PROJJSON](https://proj.org/specifications/projjson.html)
      object for arbitrary or compound CRSes
    
    There is **no default**. The CRS **MUST** be specified explicitly.
  - `edges` — interpretation of line segments between vertices.
    **MUST** be one of `"planar"` or `"spherical"`. (`planar`
    interprets edges as straight lines in the coordinate space;
    `spherical` interprets them as geodesics on the ellipsoid.)
  - `bbox_columns` — JSON array of exactly four column names, in
    order: `[xmin, ymin, xmax, ymax]`. All four columns **MUST** exist
    on the table and **MUST** be of Iceberg `double` type.

Tables with multiple geometries declare one entry per column under
`columns`. Each geometry has its own CRS, edges, and bbox-column
quadruple.

---

## 5 · Query patterns

Engines that have not yet implemented automatic predicate derivation
will get file-level pruning only when the query includes the bbox-col
predicate explicitly. The recommended form is identical to the
established GeoParquet 1.1 pattern:

```sql
SELECT id, ST_GeomFromWKB(geom_wkb) AS geom, ...
FROM table
WHERE xmin <= :qmax_x AND xmax >= :qmin_x
  AND ymin <= :qmax_y AND ymax >= :qmin_y
  AND ST_Intersects(
        ST_GeomFromWKB(geom_wkb),
        ST_MakeEnvelope(:qmin_x, :qmin_y, :qmax_x, :qmax_y)
      );
```

The bbox-col predicate is what prunes files at the Iceberg manifest
level; the `ST_Intersects` predicate filters rows within surviving
files. Both **SHOULD** be present — the bbox-col predicate matches the
geometry's bounding rectangle (which can be a loose overapproximation
for non-rectangular geometries), so `ST_Intersects` is still required
to filter rows whose bbox overlaps but whose actual geometry doesn't.

---

## 6 · Writer recommendations

For best performance, writers **SHOULD**:

- **Spatially pre-sort rows before writing files.** Without this, the
  bbox of a "globally distributed" file spans the world and the
  file-level manifest bound prunes nothing. Sort by H3, geohash, or a
  Hilbert curve so each output file covers a tight contiguous region.
- **Target ~64–512 MB per data file.** Smaller files inflate manifest
  size and lookup cost; larger files reduce pruning granularity.
- **Target ~1M rows per row group** within each parquet file. This is
  the GeoParquet 1.1 layer's pruning granularity.
- **Write data files as GeoParquet 1.1** (or 2.0 once parquet-native
  is supported by the writer). The two conventions compose; following
  GeoParquet 1.1 inside each file extends pruning to the row-group
  level after file pruning has selected the surviving files.
- **Populate per-file column statistics** (`column_sizes`,
  `value_counts`, `null_value_counts`). Standard Iceberg writers do
  this automatically; some readers (e.g. Oracle ADB) reject manifests
  that omit them.

---

## 7 · Migration to V3 native geometry

When an engine you care about gains end-to-end V3 native geometry
support, you **MAY** add a typed geometry column via Iceberg's schema
evolution:

```sql
ALTER TABLE t ADD COLUMN geom geometry(OGC:CRS84);
```

The existing `geom_wkb` column and bbox columns remain. New writes
populate both the typed column and the legacy columns. Engines that
understand V3 native geometry use the typed column directly; engines
still on V2 keep using the bbox columns + WKB. The two paths coexist
indefinitely, and the table remains portable.

When the entire engine fleet you target has V3 native support, the
bbox columns and `geom_wkb` **MAY** be dropped (Iceberg supports
column removal without rewriting data files, though the data files
themselves will still contain the dropped columns).

---

## 8 · Engine implementation hints

This section is informational, not normative. Engines implementing
this convention **MAY** offer additional optimizations:

- **Derive bbox-col predicate from `ST_Intersects`.** When a query has
  `ST_Intersects(geom, envelope)` against a geometry column named in
  the `geo` table property, the engine can synthesize the equivalent
  bbox-col overlap predicate and push it to manifest pruning. This
  optimization is also relevant for GeoParquet 1.1 covering bbox
  structs; an engine that implements it for either benefits both
  formats. As of 2026-05, no engine we tested does this.
- **Project the bbox columns away from result sets** unless the query
  explicitly references them. The bbox cols are pruning infrastructure
  and **SHOULD NOT** appear in `SELECT *` output unless requested.
- **Surface the `geo` table property to clients** via standard
  metadata queries (`SHOW TBLPROPERTIES`, `INFORMATION_SCHEMA`, etc.)
  so tooling can detect GeoIceberg V2 tables without parsing
  property strings.

---

## 9 · Reference implementation and engine support

[`testbed/v2_geo_convention.py`](testbed/v2_geo_convention.py) in this
repository writes a conformant GeoIceberg V2 table, and
[`engines/duckdb/run.py`](engines/duckdb/run.py) /
[`engines/bigquery/run.py`](engines/bigquery/run.py) probe it. The
public fixture at
`gs://cartobq-iceberg-geo-testbed/v2_geo_convention/` is a live
example you can register in any compatible engine.

**[STATUS_V2.md](./STATUS_V2.md) is the living engine-support table
for this convention** — per-engine cells for each capability defined
in this spec (R1 static metadata, R2 bbox-col pruning, R3 WKB
readback, R4 `geo` property visible, O1 auto-derive bbox). Check
there before assuming an engine delivers a particular optimization.

The companion **[STATUS_V3.md](./STATUS_V3.md)** tracks engine support
for *Iceberg V3 native* geometry/geography types — the eventual target
this convention is bridging to.

---

## 10 · Open questions

- **`primary_column` defaulting.** Should engines with multi-geometry
  awareness default to the `primary_column` when geometry-aware SQL
  functions are called without an explicit column? GeoParquet's
  behavior here is the obvious reference.
- **Geography vs geometry.** GeoParquet 1.1 conflates them via `edges`
  (planar vs spherical). Iceberg V3 separates them as distinct types.
  This V2 convention follows GeoParquet 1.1 and uses `edges`; V3
  migration is a type-token change, not a structural one.
- **CRS for the bbox columns.** This document requires the bbox
  columns to be in the same CRS as the geometry payload. Should mixed
  CRS (e.g. geometry in projected, bbox in WGS84) be permitted? We
  recommend against it for simplicity but the question is open.

Feedback: open an issue on
[`jatorre/iceberg-geo-testbed`](https://github.com/jatorre/iceberg-geo-testbed).
