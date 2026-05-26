# iceberg-geo-testbed

**A proposed convention for geospatial data on Apache Iceberg V2 — and the
cross-engine testbed that proves it works.**

This repository contains four things, in order of what you probably came for:

1. **[SPEC.md](./SPEC.md)** — *GeoIceberg V2*, a proposed convention for
   storing geospatial data in Iceberg V2 tables so engines today deliver
   file-level pruning on spatial queries without waiting for Iceberg V3
   geometry types to mature.
2. **[STATUS.md](./STATUS.md)** — living per-engine support table for
   each GeoIceberg V2 capability (which engines have which optimizations,
   what would need to change to flip each cell).
3. **The matrix** (in this file, below) — measured Iceberg geospatial
   support across DuckDB, BigQuery, Sedona / Iceberg-Spark, Snowflake,
   Databricks, and Oracle ADB on an L0–L4 ladder.
4. **The reproducible fixtures** — public GCS bucket
   `gs://cartobq-iceberg-geo-testbed/` and Python builders that anyone can
   re-run.

The narrative writeup is in [BLOG_POST.md](./BLOG_POST.md).

---

## TL;DR

> *Apache Iceberg V3 was announced in mid-2025 with native `geometry`
> types and per-file geometry bounds in the manifest. As of mid-2026
> no engine we tested supports V3 geometry end-to-end. In the
> meantime, here is a portable V2 convention that gets you file-level
> spatial pruning across every Iceberg engine we tested — modelled
> directly on how GeoParquet 1.1 solved the same problem at the
> Parquet layer.*

The convention adds, for each geometry column:

- A `geom_wkb BINARY` column (WKB payload)
- Four `DOUBLE` bbox columns (`xmin/ymin/xmax/ymax`) — these are what
  Iceberg's manifest prunes on
- A `geo` table property declaring CRS, edges, encoding, and which
  columns are bbox vs payload (same JSON shape as GeoParquet 1.1's
  `geo` metadata)

Migrate to V3's native typed `geometry` column later via `ALTER TABLE
ADD COLUMN` when your engines support it — the two paths coexist.

See [SPEC.md](./SPEC.md) for the full normative document.

---

## The matrix

Last refreshed: **2026-05-26.** Cells show the highest level reached on the
five-level support ladder (defined below).

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

### Support ladder

| Level | What it means | Failure mode below this level |
|---|---|---|
| **L0** | Engine cannot read the table | Table won't register, or geom column can't be materialized (cast gaps, type rejection) |
| **L1** | Table reads end-to-end | `SELECT *` returns rows including geo columns |
| **L2** | Spatial predicate is correct | `WHERE ST_Intersects(...)` (or equivalent V2 bbox SQL) returns the right rows, regardless of perf |
| **L3** | File-level pruning works | Manifest `lower_bounds`/`upper_bounds` actually narrow the scan to non-overlapping files |
| **L4** | Row-group / page pruning | Parquet column stats further narrow the scan *inside* the surviving files |

L4 is currently not measured by the runners.

### Access pattern: the orthogonal axis

There's a second dimension this testbed is opinionated about, separate
from L0–L4: **how does the engine discover the table?** Two families:

- **Static metadata + cloud storage** — the engine reads `metadata.json`
  at a known URL and follows the manifest paths. DuckDB, BigQuery,
  Sedona/Iceberg-Spark, Oracle ADB all expose this. Lowest-friction
  interop, no extra infra. **This is the path GeoIceberg V2 is designed
  around.**
- **Catalog-mediated** — the engine talks to a catalog server (Iceberg
  REST API, AWS Glue, Hive Metastore, etc.) which then hands it the
  metadata pointer. Databricks's Lakehouse Federation and Snowflake's
  Horizon are this kind of consumer.

Engines that *only* support catalog-mediated access show up as `n/a in
this testbed` in the matrix. Filling in those cells properly would
require Glue or Horizon as a bridge — real work that's tangential to
the V3 geometry question this testbed is asking.

---

## Sanity-check: our metadata against Apache Polaris

While debugging the engine-specific failures we deployed Apache Polaris
(the reference open-source Iceberg REST catalog, donated by Snowflake)
on a GCE VM and tried to register all three fixtures.

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

### Takeaways

Cross-checked against [icebergmatrix.org](https://icebergmatrix.org/) —
an independently maintained cross-engine Iceberg compatibility matrix —
the findings line up cleanly on Databricks/BigQuery/PyIceberg V3 status,
with two interesting deltas:

- **icebergmatrix.org says DuckDB V3 geometry = `full`**. Our hands-on
  testing shows this is overstated: type is parsed but the
  manifest-bound deserializer + the BLOB→GEOMETRY parquet cast are both
  missing, so anything beyond `SELECT COUNT(*)` errors. Filed as
  [duckdb-iceberg#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002).
- **Oracle ADB isn't in icebergmatrix.org at all.** This testbed appears
  to be the first cross-engine documentation of Oracle's stricter
  Iceberg-reader behavior.

Other takeaways from the matrix runs themselves:

- **V2 flat bbox columns work everywhere we could test (DuckDB,
  BigQuery, Sedona).** The path that's actually shippable today — and
  the path GeoIceberg V2 prescribes.
- **V2 struct-field pruning is engine-dependent.** DuckDB scans all 10
  files when the predicate hits `bbox.xmin`; BigQuery and Sedona prune
  it to 1. So the GeoParquet-1.1-style bbox struct is *not* a portable
  Iceberg pruning strategy. Flat columns are.
- **V3 native geometry is not yet ready in any engine we tested.**
  DuckDB has a bound-deserializer gap with a clear upstream fix path;
  every other engine rejects the type token earlier than that.

---

## What's in this repo

```
SPEC.md                      # GeoIceberg V2 — the recommended convention
STATUS.md                    # Living per-engine support table
BLOG_POST.md                 # Narrative writeup
README.md                    # This file

testbed/                     # Engine-agnostic fixture builders
  common.py                  # 10-region synthetic data + bound encodings
  _static_catalog.py         # Hand-writes metadata.json + manifest avro
  v2_flat_columns.py         # V2 with flat xmin/ymin/xmax/ymax columns
  v2_bbox_struct.py          # V2 with GeoParquet-1.1-style bbox struct
  v2_geo_convention.py       # The reference impl of SPEC.md
  v3_geometry.py             # V3 with native geometry(OGC:CRS84) column

engines/
  duckdb/run.py              # Local DuckDB CLI runner (working)
  bigquery/run.py            # BigLake external tables via bq CLI (working)
  sedona/                    # Spark + Sedona in Docker (working)
  snowflake/                 # Discovery + provision; account-bug blocked
  databricks/                # Discovery + V3 type probe (L0 confirmed)
  oracle/                    # Discovery + path-based probe (L0 confirmed)
  polaris/                   # Reference REST catalog on a GCE VM (validator)

docs/
  duckdb-gap.md              # Source-level analysis of the DuckDB 1.5.3 gap
  encoding.md                # V3 geometry bound byte layout per spec
  engine-matrix.md           # Detailed per-engine notes
```

---

## Public fixtures

Three reference fixtures, plus the convention reference, live in a
public GCS bucket so any engine can read them without needing this
codebase:

```
gs://cartobq-iceberg-geo-testbed/v2_flat_columns/metadata/v1.metadata.json
gs://cartobq-iceberg-geo-testbed/v2_bbox_struct/metadata/v1.metadata.json
gs://cartobq-iceberg-geo-testbed/v3_geometry/metadata/v1.metadata.json
gs://cartobq-iceberg-geo-testbed/v2_geo_convention/metadata/v1.metadata.json
```

Each fixture has the same 10,000 rows (10 disjoint regions × 1000
synthetic points each). The California-window probe should narrow to
1 file for any engine that prunes manifest bounds correctly.

---

## Quick start

```bash
git clone https://github.com/jatorre/iceberg-geo-testbed
cd iceberg-geo-testbed
brew install duckdb              # ≥ 1.5.3
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Build local fixtures (deterministic across processes; ~196 expected rows)
python -m testbed.v2_flat_columns
python -m testbed.v2_bbox_struct
python -m testbed.v2_geo_convention   # the SPEC.md reference implementation
python -m testbed.v3_geometry

# Run engine probes
python engines/duckdb/run.py
python engines/bigquery/run.py   # needs `gcloud auth login`

# Re-stage the public GCS bucket only if you changed the fixtures
python engines/bigquery/_setup.py
```

For Sedona, Polaris, and the cloud-engine discovery scripts, see each
engine's README under `engines/`.

---

## How the tests work

Each fixture builds a tiny **static Iceberg catalog** — `metadata.json`
+ manifest avro on disk, no live catalog server — over 10 disjoint
world regions × 1000 synthetic rows each. A correct file-level pruner
narrows the California-window probe query to **one** file.

The fixture seed is derived from `hashlib.sha256(region_name)` so
rebuilds across different Python processes produce byte-identical
parquet files — otherwise probe row counts would drift between engine
runs. The California-window probe always returns **196** rows.

For DuckDB we grep `Total Files Read:` from `EXPLAIN ANALYZE`. For
BigQuery we compare `total_bytes_processed` against the predicted
"1 file" and "all 10 files" sizes (each row is fixed-width —
`1000 × 8 × N_cols` bytes per file uncompressed).

---

## Adjacent: GeoParquet (no Iceberg)

Same engines, just `read_parquet(...)` directly. Documented here
because it's the alternative path our consumers actually use today.

| Engine | GeoParquet 1.1 per-row-group bbox | File-level pruning across many files |
|---|---|---|
| **DuckDB 1.5.3** | ✅ — prunes row groups within each file | ❌ — opens every file's footer; no manifest equivalent |
| **Snowflake** | ❓ | ❓ |
| **BigQuery** | ❓ | ❓ |

The motivating problem: ~90s cold for an SF-bbox query over the
512-file Overture buildings dataset on DuckDB. Iceberg V3's per-file
geometry bounds are the architectural fix; the GeoIceberg V2
convention is the bridge while V3 catches up.

---

## Contributing

Open an issue with the engine, version, and minimal repro. PRs welcome
for new engine runners, for upstream fixes that land back here as a
level-up in the matrix, or for filling in the `❓` cells.

For the SPEC itself, the open questions are listed at the bottom of
[SPEC.md](./SPEC.md). Feedback there is exactly what would help.

## License

Apache 2.0 — see [LICENSE](./LICENSE).
