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

## Conclusions matrix

Last refreshed: **2026-05-23.** Cells show the highest level reached.

| Engine / version | V2 flat-bbox cols | V2 `bbox` struct | V3 native `geometry` |
|---|---|---|---|
| **DuckDB 1.5.3**       | **L3** — prunes to 1/10 files | **L2** — correct, but no file pruning (struct-field gap) | **L0** — registers + `COUNT(*)` works, but `SELECT geom` errors (BLOB→GEOMETRY cast + manifest-bound deserializer both missing). See [docs/duckdb-gap.md](docs/duckdb-gap.md). |
| **BigQuery / BigLake** | **L3** — 32 KB scanned vs 320 KB baseline (1/10 files) | **L3** — prunes through struct fields too (improvement over DuckDB!) | **L0** — `CREATE EXTERNAL TABLE` rejects: `Unknown Iceberg type "geometry(OGC:CRS84)"`. See [engines/bigquery/README.md](engines/bigquery/README.md). |
| **Snowflake**          | ⏸ blocked | ⏸ | ⏸ | Two accounts tried. CARTO dev (shared): can't `CREATE EXTERNAL VOLUME` from `TEST_ROLE`. Personal trial on GCP-EU: have ACCOUNTADMIN, external volume passes `VERIFY` (all of write/read/list/delete PASSED), `SNOWFLAKE.MONITORING.ICEBERG_ACCESS_ERRORS` is empty for the fresh volume — yet every `CREATE ICEBERG TABLE` (managed *or* unmanaged) fails with `091369`. Snowflake backend bug; needs a support ticket. See [engines/snowflake/README.md](engines/snowflake/README.md). |
| **Sedona 1.6.1 + Iceberg-Spark 1.7.1** | **L3** — 1 of 10 files | **L3** — prunes through struct fields | **L0** — `Cannot parse type string to primitive: geometry(OGC:CRS84)`. Sedona itself also can't *write* V3 geometry: `iceberg-spark-runtime` rejects Sedona's Geometry UDT (`UnsupportedOperationException: User-defined types are not supported`). Our V2 numeric bound encoding is bit-identical to Iceberg-Spark's. See [engines/sedona/README.md](engines/sedona/README.md). |
| **Databricks (DBSQL 2026.10)** | n/a — structural | n/a | **L0** — `[UNSUPPORTED_DATATYPE] Unsupported data type "GEOMETRY"` (also rejects `GEOGRAPHY`). DBSQL can't read static `metadata.json` directly either (requires Iceberg REST / Glue / Unity Catalog mediation), so the v2 cells are "structural n/a". `ST_*` spatial functions exist but return strings, not typed geometries. See [engines/databricks/README.md](engines/databricks/README.md). |
| **PyIceberg 0.11.1**   | reads | reads | ⚠️ V3 read landed; no `GeometryType` writer | Tracking [iceberg-python#1818](https://github.com/apache/iceberg-python/issues/1818). |
| **DuckLake 1.0**       | — | — | "forthcoming" | Re-test each release. |

### What you can already say from this

- **V2 flat bbox columns work everywhere.** Both DuckDB and BigQuery prune
  correctly. This is the path that's actually shippable today.
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
