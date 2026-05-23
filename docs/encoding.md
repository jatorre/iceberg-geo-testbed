# Iceberg V3 — geometry/geography bound byte encoding

Reference notes pulled from the Apache Iceberg V3 spec. Used by every
engine's bound decoder; recording them here so test runners can encode the
same way.

## Numeric column bounds (existing V1/V2 mechanism)

Stored as the **single-object serialization** of the column's value type. For
the types we use:

| Iceberg type | Bytes | Layout |
|---|---|---|
| `int` | 4 | LE int32 |
| `long` | 8 | LE int64 |
| `float` | 4 | LE IEEE 754 |
| `double` | 8 | LE IEEE 754 |
| `string` | n | UTF-8 |

## Geometry / geography column bounds (V3)

`lower_bound` and `upper_bound` are **points** in the bounding-box plane. The
serialization is the X/Y/Z/M coordinates concatenated as 8-byte LE doubles:

```
lower_bound = [xmin: f64 LE][ymin: f64 LE]                      # 16 bytes (XY)
            = [xmin: f64 LE][ymin: f64 LE][zmin: f64 LE]        # 24 bytes (XYZ)
            = [xmin: f64 LE][ymin: f64 LE][zmin: f64 LE][mmin]  # 32 bytes (XYZM)

upper_bound = [xmax: f64 LE][ymax: f64 LE] ...                   (mirror)
```

Rules:
- If a dimension's values are all null or NaN, that dimension is omitted from
  both bounds (so XY-only files have 16-byte bounds even if the schema is
  XYZM-capable).
- If either X or Y is missing entirely, no bound is produced.
- For `geography` only, `xmin > xmax` is legal and means the bbox wraps the
  antimeridian (matching X satisfies `X ≥ xmin OR X ≤ xmax`).

## Stored where

In the manifest avro file, the `data_file.lower_bounds` and `upper_bounds`
fields are `map(integer, binary)` — field-id keys, opaque blob values. The
encoding above is what those blobs contain for geometry/geography fields.

## Implementations

| Engine | Reads geometry bounds | Notes |
|---|---|---|
| Apache Sedona | yes | Reference implementation (Havasu lineage). |
| Snowflake | yes | Per V3 GA announcement; used for spatial pruning. |
| DuckDB 1.5.3 | **no** | Schema/data path wired; `DeserializeValue` lacks the geometry case. See [duckdb-gap.md](./duckdb-gap.md). |
| BigLake / BigQuery | unverified | Listed as supporting V3 geo; needs runner. |
| PyIceberg 0.11.1 | partial | Read side; no `GeometryType` in writer surface. |
