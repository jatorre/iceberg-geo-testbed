# Picking up where we left off

State as of **2026-05-23.**

## What's done

- **DuckDB 1.5.3** matrix: v2_flat=L3, v2_struct=L2, v3=L0.
  `python engines/duckdb/run.py`. Source-level gap in `docs/duckdb-gap.md`.
- **BigQuery / BigLake** matrix: v2_flat=L3, v2_struct=L3 (prunes through
  struct fields, unlike DuckDB!), v3=L0 (rejects `geometry(OGC:CRS84)` at
  table registration). `python engines/bigquery/run.py`.
- Fixtures staged to a **public** GCS bucket so anyone can repro:
  `gs://cartobq-iceberg-geo-testbed/<table>/{metadata,data}/`. Mirror in
  `gs://cartobq-iceberg-geo-testbed-eu/` (provisioned during the Snowflake
  attempt). Both have `allUsers:objectViewer`.
- README restructured around an **L0–L4 support ladder** per (engine, fixture).
- Fixture seed is now deterministic across processes
  (`testbed.common.stable_seed`); fresh rebuilds will produce identical
  parquet bytes. Expected probe rows = **196**.

## Snowflake — blocked (two different walls)

Tried two accounts, both blocked. Full diagnostic in
`engines/snowflake/README.md`. Headline:

- **Account A** (`SXA81489`, CARTO dev shared, AWS_US_EAST_1): `TEST_ROLE`
  can't `CREATE EXTERNAL VOLUME` (needs ACCOUNTADMIN).
- **Account B** (`KJEIDXA-IK05112`, personal trial, GCP_EUROPE_WEST2): we
  have ACCOUNTADMIN, the external volume verifies clean, but **every**
  `CREATE ICEBERG TABLE` (managed and unmanaged) errors with
  `091369: Query needs to be retried to setup external volume`. Retries
  don't help. No deeper error in `QUERY_HISTORY`. Reproduces with
  Snowflake-managed Iceberg too, so it's not our metadata.

Next-step options when picking this up:
1. Open the Snowsight UI on account B — the create-table wizard sometimes
   surfaces a setup banner the SQL-only path doesn't.
2. File a Snowflake support ticket with error 091369. Repro SQL is in
   `engines/snowflake/_provision.py` + the comments in the README.
3. Try a Snowflake account on AWS — the GCP fresh-account setup may have
   a known issue this avoids.

## Sedona (untouched)

The reference implementation. Highest validation value: produce the same
fixtures via Sedona's V3 writer and diff-avro against our manifest avro to
confirm canonical bound encoding.

## DuckDB upstream PRs

Two separate gaps to file:
- `IcebergValue::DeserializeValue` (in
  `src/core/expression/iceberg_value.cpp`) is missing the GEOMETRY/GEOGRAPHY
  branch — see `docs/duckdb-gap.md`.
- A BLOB→GEOMETRY cast is missing in the parquet reader path. You hit this
  *before* the bound deserializer if you `SELECT geom` — even without a
  spatial predicate.

## How to run

```bash
brew install duckdb              # ≥ 1.5.3
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Build local fixtures (DuckDB target — deterministic across processes)
python -m testbed.v2_flat_columns
python -m testbed.v2_bbox_struct
python -m testbed.v3_geometry

python engines/duckdb/run.py
python engines/bigquery/run.py   # needs `gcloud auth login`

# Re-stage the public GCS bucket only if you changed the fixtures:
python engines/bigquery/_setup.py
```

## Companion project

Spun out of [`jatorre/tilerPrototype`](https://github.com/jatorre/tilerPrototype).
