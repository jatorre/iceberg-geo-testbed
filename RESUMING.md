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
  `CREATE ICEBERG TABLE` (managed *and* unmanaged) errors with
  `091369: Query needs to be retried to setup external volume`.
  `SNOWFLAKE.MONITORING.ICEBERG_ACCESS_ERRORS` is empty for the fresh
  volume — so 091369 is upstream of the GCS call, internal to Snowflake.
  Worth knowing: when the SA *didn't* have `objectAdmin` on the bucket,
  that view *did* log a `403 storage.objects.create` from GCS — confirming
  Snowflake's Iceberg provisioning DOES require write perms on the bucket
  even for read-only Iceberg tables. Once we granted the SA `objectAdmin`,
  cloud-side errors stopped but 091369 persists.

Next step is filing a Snowflake support ticket — we've ruled out everything
external (IAM, bucket region, our metadata, catalog integration choice).

## Sedona — done (with a twist)

Sedona 1.6.1 + iceberg-spark-runtime 1.7.1 on Spark 3.4.1, via the
`apache/sedona:1.6.1` Docker image.

- **Probing our hand-written fixtures**: v2_flat=L3, v2_struct=L3 (prunes
  through struct fields, matching BigQuery), v3=L0. The v3 read error is
  the same shape as BigQuery's: `Cannot parse type string to primitive:
  geometry(OGC:CRS84)`. Confirms our hand-written V3 metadata is ahead of
  what the official toolchain accepts.
- **Ground-truth manifest diff** (the original motivation): Sedona's
  Iceberg-Spark writer produces a V2 manifest with **bit-identical
  little-endian IEEE 754 doubles** for numeric `lower_bounds`/
  `upper_bounds`. Validates our hand-written encoding.
- **Sedona can't write V3 geometry either**: `iceberg-spark-runtime 1.7.1`
  rejects Sedona's Geometry UDT at the `SparkTypeVisitor` layer with
  `UnsupportedOperationException: User-defined types are not supported`.
  So Sedona is not yet the "reference V3 writer" we hoped — there's a real
  upstream gap in `iceberg-spark` itself.

`engines/sedona/run.sh build|probe` is the entry point; details in
`engines/sedona/README.md`.

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
