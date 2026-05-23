# Picking up where we left off

State as of **2026-05-23**.

## What's done

- Repo skeleton, license, README with the cross-engine conclusions matrix.
- Three test fixtures (`testbed/v2_flat_columns.py`, `v2_bbox_struct.py`, `v3_geometry.py`) that all build cleanly and produce static Iceberg catalogs on disk.
- DuckDB engine runner (`engines/duckdb/run.py`) — green on the v2-flat row, documents the v2-struct and v3-geometry gaps. Run with:
  ```bash
  python -m testbed.v2_flat_columns
  python -m testbed.v2_bbox_struct
  python -m testbed.v3_geometry
  python engines/duckdb/run.py
  ```
- Source-level gap analysis for the DuckDB V3 geometry deserializer is in `docs/duckdb-gap.md`. Likely upstream PR target.

## Next up

**Fill in the Snowflake and BigQuery rows of the matrix.** Credentials are in the gcloud secret `carto-dev-database-credentials` on the `cartobq` project — same one the [tilerPrototype](https://github.com/jatorre/tilerPrototype) Go server reads.

Prereq: `gcloud auth login` (the tool-driven session can't do this interactively).

### BigQuery / BigLake

1. Stage the ten regional parquet files (built by `testbed.v2_flat_columns`) to a GCS bucket.
2. Create a BigLake Iceberg table pointing at our hand-written `metadata.json`. Check whether BigLake's "object storage catalog" mode accepts a static metadata path.
3. Run the same probe query (`WHERE xmin <= -118 AND xmax >= -125 ...`) and read `total_bytes_processed` or `total_partitions_processed` from `INFORMATION_SCHEMA.JOBS_BY_PROJECT` as the pruning telemetry.
4. Repeat for `v2_bbox_struct` and `v3_geometry`. The interesting question: does BigQuery honor V3 geometry bounds for `ST_INTERSECTS` pushdown?

### Snowflake

1. Use the Snowflake credentials from the secret. The tilerPrototype's local Snowflake ADBC fork compiles but errors at runtime against the newer driverbase — for THIS repo we should use the official Python connector (`snowflake-connector-python`), not the ADBC fork.
2. Stage parquet to a Snowflake external stage backed by GCS or S3.
3. `CREATE ICEBERG TABLE ... CATALOG = 'OBJECT_STORE_CATALOG' METADATA_FILE_PATH = '...'` — verify whether this works against our static metadata, or whether we need an Iceberg REST catalog.
4. Run probe; capture pruning via `SYSTEM$EXPLAIN_PLAN_JSON()` or query profile.

### Sedona (lower priority, but high value)

The reference implementation. A Docker-based runner that produces the same fixtures via Sedona's writer would give us a ground-truth manifest avro to diff against our hand-written one — confirming the canonical V3 geometry bound encoding empirically.

## DuckDB upstream PR

`docs/duckdb-gap.md` has the source-level analysis. The missing case in `src/core/expression/iceberg_value.cpp` `IcebergValue::DeserializeValue` is the actionable line. Worth opening as an issue first, then a PR, with this repo's `v3_geometry.py` as the reproducible fixture.

## Companion project

This repo was spun out of [`jatorre/tilerPrototype`](https://github.com/jatorre/tilerPrototype) — the broader prototype where this exploration started. The tilerPrototype's `docs/widgets-design.md` and the GeoParquet endpoint (`go/geoparquet.go`) are the consumers that ultimately benefit from this work.
