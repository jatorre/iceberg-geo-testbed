# DuckDB 1.5.3 — geometry bound deserialization gap

DuckDB 1.5.3 (released 2026-05-20) ships initial `GEOMETRY` support for
Iceberg tables. Schema parsing works — pointing `iceberg_scan(...)` at a
metadata.json that declares a column as `geometry(OGC:CRS84)` correctly
produces a column of type `GEOMETRY('OGC:CRS84')`. Data scan works — the WKB
bytes in the parquet are surfaced as DuckDB geometry values.

**The gap is at the file-pruning step.** When the planner reads the manifest
to evaluate per-file `lower_bound` / `upper_bound` against the query
predicate, it dispatches to `IcebergValue::DeserializeValue(blob, type)` —
which has no branch for `LogicalTypeId::GEOMETRY` (or `GEOGRAPHY`). The
function falls through to `DeserializeError`, surfaced as:

```
Invalid Configuration Error: Column geom lower bound deserialization failed:
Failed to deserialize blob ... of size N, attempting to produce value of
type 'GEOMETRY('OGC:CRS84')'
```

This happens regardless of the bound byte layout the writer chose (we tried
the spec's 16-byte packed `(xmin, ymin)` doubles and 21-byte WKB POINT — both
hit the same branch).

## Repro

```bash
brew install duckdb           # ≥ 1.5.3
python -m testbed.v3_geometry  # writes data/v3_geometry/metadata/v1.metadata.json

duckdb -c "LOAD iceberg; LOAD spatial; \
  SELECT COUNT(*) FROM iceberg_scan('data/v3_geometry/metadata/v1.metadata.json') \
  WHERE ST_Intersects(geom, ST_MakeEnvelope(-125, 32, -115, 42));"
```

## Source-level location

- `src/core/expression/iceberg_value.cpp` :: `IcebergValue::DeserializeValue`
  — the switch over `LogicalTypeId`. Add a case for `GEOMETRY` (and
  `GEOGRAPHY`) that decodes the bound blob per the
  [Iceberg V3 spec](https://iceberg.apache.org/spec/#binary-single-value-serialization)
  geometry encoding (X then Y as 8-byte little-endian doubles, optional Z, M).
- `src/planning/iceberg_multi_file_list.cpp` ::
  `IcebergPredicateStats::DeserializeBounds` — call site; should not need
  changes.

## Spec — geometry bound encoding (V3)

For `geometry` and `geography`, the manifest bound for a file is a point in
the X/Y/Z/M plane:

| Field | Bytes | Value |
|---|---|---|
| X | 8 | little-endian IEEE 754 double, xmin (lower) / xmax (upper) |
| Y | 8 | little-endian IEEE 754 double, ymin (lower) / ymax (upper) |
| Z (optional) | 8 | zmin / zmax — omit if no Z |
| M (optional) | 8 | mmin / mmax — omit if no M |

For `geography`, the longitude bound may wrap the antimeridian — `xmin > xmax`
in that case means "X ≥ xmin OR X ≤ xmax".

## Proposed PR shape

1. `DeserializeValue`: add `GEOMETRY` / `GEOGRAPHY` cases that parse the
   blob into a `geo::Point` (or whatever the spatial module's lightweight
   value is) and return it.
2. `IcebergPredicateStats`: add an `ApplyGeometryFilter` overload that
   handles `ST_Intersects` against a point-pair bound.
3. Test: a fixture identical to `testbed/v3_geometry.py` in this repo.

Happy to drive this once we have a Sedona-produced reference fixture to diff
against (see `engines/sedona/`).
