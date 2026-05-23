# Engine support matrix

Living table of file-level pruning behavior, per engine and per schema
variant. Update via PR when a new engine runner lands or an upstream fix
shifts a row.

## Schema variants under test

- **V2-flat**: V2 Iceberg, top-level `xmin/ymin/xmax/ymax` double columns,
  per-file `lower_bounds`/`upper_bounds` on each.
- **V2-struct**: V2 Iceberg, single `bbox` STRUCT column (GeoParquet 1.1
  covering convention), per-file bounds on the struct's leaves.
- **V3-geom**: format-version 3, native `geometry(OGC:CRS84)` column with WKB
  data, per-file 16-byte (xmin, ymin)/(xmax, ymax) geometry bounds.

Probe query: a narrow California window that should match exactly one of the
ten regional files.

## Matrix

| Engine | V2-flat | V2-struct | V3-geom | Source |
|---|:---:|:---:|:---:|---|
| DuckDB 1.5.3 | ✅ 1 file | ❌ 10 files | ❌ deser error | `engines/duckdb/` |
| Apache Sedona | ? | ? | ? | TODO `engines/sedona/` |
| Snowflake | ? | ? | ? | TODO `engines/snowflake/` |
| BigQuery / BigLake | ? | ? | ? | TODO `engines/bigquery/` |

Legend:
- ✅ N files = pruner narrowed to N files (expected: 1 for the probe)
- ❌ N files = pruner did not work; full scan
- ❌ deser error = bound deserialization failed before pruning
- ? = not yet measured
