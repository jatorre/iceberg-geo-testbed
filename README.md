# iceberg-geo-testbed

A cross-engine testbed for **Apache Iceberg geospatial support** — V2 and V3 — plus the adjacent GeoParquet path. The goal is one reproducible place to ask, per engine: *can it query geo data through Iceberg today, and does it prune files for spatial predicates?*

> Iceberg V3 (mid-2025) introduced native `geometry`/`geography` types with per-file `lower_bounds`/`upper_bounds` in the manifest. The spec promises that a query like `WHERE ST_Intersects(geom, bbox)` can prune non-overlapping files before touching their data. This repo verifies who actually delivers.

## Support ladder

We rate each (engine × fixture) cell on a five-level ladder. A given engine
can fall off the ladder for different reasons in V2 vs V3, which is exactly
the point — the matrix tells you *where* support breaks, not just whether
it does.

| Level | What it means | Failure mode below this level |
|---|---|---|
| **L0** | Engine cannot read the table | Table won't register, or geom column can't be materialized (cast gaps, type rejection) |
| **L1** | Table reads end-to-end | `SELECT *` returns rows including geo columns |
| **L2** | Spatial predicate is correct | `WHERE ST_Intersects(...)` (or equivalent V2 bbox SQL) returns the right rows, regardless of perf |
| **L3** | File-level pruning works | Manifest `lower_bounds`/`upper_bounds` actually narrow the scan to non-overlapping files |
| **L4** | Row-group / page pruning | Parquet column stats further narrow the scan *inside* the surviving files |

L4 is currently not measured by the runners — it would require digging into
per-file row-group telemetry. Today the matrix tops out at L3.

## Access pattern: the orthogonal axis

There's a second dimension this testbed is opinionated about, separate
from L0–L4: **how does the engine discover the table?** Two families:

- **Static metadata + cloud storage** — the engine reads `metadata.json`
  at a known URL and follows the manifest paths. DuckDB, BigQuery,
  Sedona/Iceberg-Spark, Oracle ADB all expose this. Lowest-friction
  interop, no extra infra.
- **Catalog-mediated** — the engine talks to a catalog server (Iceberg
  REST API, AWS Glue, Hive Metastore, etc.) which then hands it the
  metadata pointer. Databricks Lakehouse Federation and Snowflake's
  Horizon are this kind of consumer.

This testbed is built around the static-metadata path because it's the
most engine-agnostic and the easiest to share publicly (just a GCS
bucket URL). Engines that *only* support catalog-mediated access show
up as `n/a in this testbed` in the matrix below — they likely support
V2/V3 fine via their preferred catalog, just not the testbed's bare-URL
path. Per [icebergmatrix.org](https://icebergmatrix.org/) and the
official docs:

- **Databricks** consumes Iceberg only via **Unity Catalog, AWS Glue,
  HMS, or Snowflake Horizon** — no generic REST consumer (we proved
  this by trying `CREATE CONNECTION TYPE iceberg` / `ICEBERG_REST`,
  both rejected as unsupported types).
- **Oracle ADB**'s REST integration only recognizes specific cloud
  endpoints (Snowflake-Polaris-hosted, AWS Glue) — not a self-hosted
  REST endpoint at a raw IP.

So a "Databricks blocked" cell in the matrix below is a *testbed
methodology* result, not "Databricks doesn't support V2". Filling in
those cells properly would require Glue or Horizon as a bridge — real
work that's tangential to the V3 geometry question this testbed is
asking.

## Conclusions matrix

Last refreshed: **2026-05-24.** Cells show the highest level reached.

| Engine / version | V2 flat-bbox cols | V2 `bbox` struct | V3 native `geometry` |
|---|---|---|---|
| **DuckDB 1.5.3**       | **L3** — prunes to 1/10 files | **L2** — correct, but no file pruning (struct-field gap) | **L0** — registers + `COUNT(*)` works, but `SELECT geom` errors (BLOB→GEOMETRY cast + manifest-bound deserializer both missing). See [docs/duckdb-gap.md](docs/duckdb-gap.md). |
| **BigQuery / BigLake** | **L3** — 32 KB scanned vs 320 KB baseline (1/10 files) | **L3** — prunes through struct fields too (improvement over DuckDB!) | **L0** — `CREATE EXTERNAL TABLE` rejects: `Unknown Iceberg type "geometry(OGC:CRS84)"`. See [engines/bigquery/README.md](engines/bigquery/README.md). |
| **Snowflake**          | ⏸ blocked | ⏸ blocked | ⏸ blocked — two accounts tried. CARTO dev (shared): can't `CREATE EXTERNAL VOLUME` from `TEST_ROLE`. Personal trial on GCP-EU: have ACCOUNTADMIN, external volume passes `VERIFY` (all of write/read/list/delete PASSED), `SNOWFLAKE.MONITORING.ICEBERG_ACCESS_ERRORS` is empty for the fresh volume — yet every `CREATE ICEBERG TABLE` (managed *or* unmanaged) fails with `091369`. Snowflake backend bug; needs a support ticket. Per [icebergmatrix.org](https://icebergmatrix.org/) Snowflake claims `full` V3 geometry support in public preview — couldn't verify due to the bug. See [engines/snowflake/README.md](engines/snowflake/README.md). |
| **Sedona 1.6.1 + Iceberg-Spark 1.7.1** | **L3** — 1 of 10 files | **L3** — prunes through struct fields | **L0** — `Cannot parse type string to primitive: geometry(OGC:CRS84)`. Sedona itself also can't *write* V3 geometry: `iceberg-spark-runtime` rejects Sedona's Geometry UDT (`UnsupportedOperationException: User-defined types are not supported`). Our V2 numeric bound encoding is bit-identical to Iceberg-Spark's. See [engines/sedona/README.md](engines/sedona/README.md). |
| **Databricks (DBSQL 2026.10)** | n/a in this testbed | n/a | **L0** — `[UNSUPPORTED_DATATYPE] Unsupported data type "GEOMETRY"` (also rejects `GEOGRAPHY`). Caveat on the V2 cells: Databricks **does** fully support reading Iceberg V2 — *but only via specific named catalog providers* (Unity, AWS Glue, Hive Metastore, Snowflake Horizon, per the [Databricks Iceberg announcement](https://www.databricks.com/blog/announcing-full-apache-iceberg-support-databricks)). It has *no* generic Iceberg REST catalog client and *no* static-`metadata.json`-on-bucket path. Our public-bucket testbed doesn't fit those slots without first re-registering tables in Glue or Horizon. `ST_*` spatial functions exist but return strings, not typed geometries. See [engines/databricks/README.md](engines/databricks/README.md). |
| **Oracle ADB 26ai (23.26.2.2.0)** | **L0** | **L0** | **L0** — all three fail with `ORA-20000: Iceberg parameter error / Failed to generate column list`. Network + public-bucket access verified (`LIST_OBJECTS` works). Path-based Iceberg registration is the documented syntax — Oracle's reader just doesn't accept pyiceberg-emitted manifests. Spark/Athena/Snowflake-produced metadata is what Oracle tests against. See [engines/oracle/README.md](engines/oracle/README.md). |
| **PyIceberg 0.11.1**   | reads | reads | ⚠️ V3 read landed; no `GeometryType` writer | Tracking [iceberg-python#1818](https://github.com/apache/iceberg-python/issues/1818). |
| **DuckLake 1.0**       | — | — | "forthcoming" | Re-test each release. |

### Sanity-check: our metadata against Apache Polaris

We deployed Apache Polaris (the reference open-source Iceberg REST
catalog, donated by Snowflake) on a GCE VM and tried to register all
three fixtures.

- **V2 fixtures: 200 OK.** Our hand-written V2 metadata is spec-compliant
  by Polaris's standards.
- **V3 fixture initially returned 400** — `Cannot parse missing long:
  next-row-id`. pyiceberg 0.11.1 doesn't emit the V3-required
  `next-row-id` / `row-lineage` fields. Patched `_static_catalog.py`
  to emit them when `format_version_in_metadata=3`; V3 now also returns
  200 OK on registration.

So Polaris caught a real V3 spec gap that no other engine we tested
flagged (they all reject the V3 metadata higher up — at the geometry
type token — before reaching `next-row-id` validation). Worth running
`engines/polaris/_setup.py` whenever `_static_catalog.py` changes. See
[engines/polaris/README.md](engines/polaris/README.md).

We also tried using Polaris as a *bridge* for Oracle and Databricks
(both of which require catalog-mediated access). Neither accepts a
self-hosted Polaris endpoint:

- Oracle ADB's REST-catalog support seems to only recognize known cloud
  endpoints (Snowflake-Polaris, AWS Glue) — not generic
  Iceberg-REST-at-an-IP.
- Databricks's `CREATE CONNECTION TYPE iceberg` errors with
  `CONNECTION_TYPE_NOT_SUPPORTED` (Glue/Unity/Snowflake-Horizon only).

### What you can already say from this

This repo's findings cross-checked against
[icebergmatrix.org](https://icebergmatrix.org/) — an independently
maintained cross-engine Iceberg compatibility matrix that we discovered
late in the session — line up cleanly on Databricks/BigQuery/PyIceberg
V3 status, with two interesting deltas:

- **icebergmatrix.org says DuckDB V3 geometry = `full`** ("GEOMETRY type
  support added to the DuckDB Iceberg extension in v1.5.2"). Our
  hands-on testing shows this is overstated: the type is parsed but the
  manifest-bound deserializer + the BLOB→GEOMETRY parquet cast are both
  missing, so anything beyond `SELECT COUNT(*)` errors. Filed as
  [duckdb-iceberg#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002).
- **Oracle ADB isn't in icebergmatrix.org at all.** This testbed appears
  to be the first cross-engine documentation of Oracle's stricter
  Iceberg-reader behavior (rejects pyiceberg-emitted manifests despite
  same files being spec-compliant per Polaris).

Other takeaways from the matrix runs themselves:

- **V2 flat bbox columns work everywhere we could test (DuckDB, BigQuery,
  Sedona).** Both DuckDB and BigQuery prune correctly. This is the
  path that's actually shippable today.
- **V2 struct-field pruning is engine-dependent.** DuckDB scans all 10 files
  when the predicate hits `bbox.xmin`; BigQuery prunes the same predicate to
  1. So "GeoParquet-1.1-style bbox struct" is *not* a portable pruning
  strategy — engines vary.
- **V3 native geometry is not yet ready in either engine measured.** DuckDB
  has a bound-deserializer gap with a clear upstream fix path; BigQuery's
  BigLake reader doesn't know the type token at all.

### Adjacent: GeoParquet (no Iceberg)

Same engines, just `read_parquet(...)` directly. Documented here because
it's the alternative path our consumers actually use today.

| Engine | GeoParquet 1.1 per-row-group bbox | File-level pruning across many files |
|---|---|---|
| **DuckDB 1.5.3** | ✅ — prunes row groups within each file | ❌ — opens every file's footer; no manifest equivalent |
| **Snowflake** | ❓ | ❓ |
| **BigQuery** | ❓ | ❓ |

The motivating problem: ~90s cold for an SF-bbox query over the 512-file
Overture buildings dataset on DuckDB. Iceberg V3's per-file geometry bounds
are the architectural fix; this repo tracks who has actually implemented it.

## What's in here

```
testbed/                     # Engine-agnostic test fixtures
  v2_flat_columns.py         # V2 Iceberg with flat xmin/ymin/xmax/ymax + per-file bounds
  v2_bbox_struct.py          # V2 with GeoParquet-1.1-style bbox struct column
  v3_geometry.py             # V3 with native geometry(OGC:CRS84) column
  common.py                  # 10-region fixture data + bound-encoding helpers
  _static_catalog.py         # Hand-writes metadata.json + manifest avro + manifest-list

engines/
  duckdb/run.py              # Local DuckDB CLI (working)
  bigquery/run.py            # BigLake external tables via the bq CLI (working)
  snowflake/                 # Discovery only; new admin account being set up
  sedona/                    # Planned — reference implementation
  bigquery/_setup.py         # Build gs:// metadata + gsutil rsync to public bucket

docs/
  duckdb-gap.md              # Source-level analysis of the DuckDB 1.5.3 geometry-bound gap
  encoding.md                # V3 geometry bound byte layout per spec
  engine-matrix.md           # Detailed per-engine notes
```

## How the tests work

Each fixture builds a tiny **static Iceberg catalog** — `metadata.json` +
manifest avro on disk, no live catalog server — over 10 disjoint world
regions × 1000 synthetic rows each. A correct file-level pruner narrows the
California-window probe query to **one** file.

The fixture seed is derived from `hashlib.sha256(region_name)` so rebuilds
across different Python processes produce byte-identical parquet files —
otherwise probe row counts would drift between engine runs. The
California-window probe should always return **196** rows.

For DuckDB we grep `Total Files Read:` from `EXPLAIN ANALYZE`. For BigQuery
we compare `total_bytes_processed` against the predicted "1 file" and "all
10 files" sizes (each row is fixed-width — `1000 × 8 × N_cols` bytes per
file uncompressed).

The fixtures are also staged to a public GCS bucket so other engines (and
other people) can read the same metadata without re-running the build:

```
gs://cartobq-iceberg-geo-testbed/<table>/metadata/v1.metadata.json
gs://cartobq-iceberg-geo-testbed/<table>/data/*.parquet
```

## Running it locally

```bash
brew install duckdb              # ≥ 1.5.3
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Build the three local fixture tables (DuckDB target)
python -m testbed.v2_flat_columns
python -m testbed.v2_bbox_struct
python -m testbed.v3_geometry

# DuckDB matrix
python engines/duckdb/run.py

# BigQuery matrix (needs `gcloud auth login` first; reads the public bucket)
python engines/bigquery/run.py
```

To refresh the GCS bucket (only needed when you change the testbed code):

```bash
python engines/bigquery/_setup.py
```

## Why this exists

In the [`tilerPrototype`](https://github.com/jatorre/tilerPrototype) work
the practical wall against GeoParquet for "many files, fast bbox query"
was always: DuckDB has to walk every file's footer to evaluate row-group
stats — 90+ seconds against an Overture-scale tree on S3. Iceberg V3's
per-file geometry bounds in the manifest are the right architectural fix,
but engine support is incomplete and inconsistent. This repo isolates the
cross-engine verification from the prototype so it can collect
collaborators and drive upstream conversations on its own pace.

## Contributing

Open an issue with the engine, version, and minimal repro. PRs welcome for
new engine runners, for upstream fixes that land back here as a level-up
in the matrix, or for filling in the `❓` cells.

## License

Apache 2.0 — see [LICENSE](./LICENSE).
