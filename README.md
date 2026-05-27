# iceberg-geo-testbed

**A proposed convention for geospatial data on Apache Iceberg V2 — and the
cross-engine testbed that proves it works.**

This repository contains four things, in order of what you probably came for:

1. **[SPEC.md](./SPEC.md)** — *GeoIceberg V2*, a proposed convention for
   storing geospatial data in Iceberg V2 tables so engines today deliver
   file-level pruning on spatial queries without waiting for Iceberg V3
   geometry types to mature.
2. **[STATUS_V2.md](./STATUS_V2.md)** and **[STATUS_V3.md](./STATUS_V3.md)** —
   two living per-engine support tables. V2 tracks the GeoIceberg V2
   convention's capabilities; V3 tracks engine implementation of
   Iceberg V3's native geometry/geography types.
3. **The matrix** (in this file, below) — measured Iceberg geospatial
   support across DuckDB, BigQuery, Sedona / Iceberg-Spark, Snowflake,
   Databricks, and Oracle ADB on an L0–L4 ladder.
4. **The reproducible fixtures** — public GCS bucket
   `gs://cartobq-iceberg-geo-testbed/` and Python builders that anyone can
   re-run. The V3 fixture (`v2_geo_convention` for V2 + `v3_geometry`
   for native V3) is intended as a **reference catalog** that
   spec-compliant V3 readers should accept; engines that reject it
   have engine-side gaps to file, not catalog-side bugs to fix.

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
| **DuckDB 1.5.3**       | **L3** — prunes to 1/10 files | **L2** — correct, but no file pruning (struct-field gap) | **L2** — schema parses, `COUNT(*)` works, `SELECT geom` returns typed geometries, `ST_AsText(geom)` produces clean WKT. **Cross-verified L2 against both our hand-written V3 *and* Snowflake's own managed V3 table** — same level, same failure pattern on `ST_Intersects`, proves cross-engine V3 interop works for the parts DuckDB supports. Only remaining gap is the manifest bound deserializer (`IcebergValue::DeserializeValue` has no GEOMETRY branch). Filed at [duckdb-iceberg#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002). |
| **BigQuery / BigLake** | **L3** — 32 KB scanned vs 320 KB baseline (1/10 files) | **L3** — prunes through struct fields too (improvement over DuckDB!) | **L0** — `CREATE EXTERNAL TABLE` rejects: `Unknown Iceberg type "geometry(OGC:CRS84)"`. See [engines/bigquery/README.md](engines/bigquery/README.md). |
| **Snowflake 10.19.100 (GCP-EU)** | **L3** — pruning via manifest `record_count` (`bytes_scanned=0`) | **L3** — same; Snowflake variant access `bbox:xmin::FLOAT` | **L3** *via Snowflake-managed write path* — `CREATE ICEBERG TABLE … GEOMETRY ICEBERG_VERSION=3` works; spatial predicate returns correct 1000 rows; `bytes_scanned=0` confirms manifest geometry-bound pruning. **L0** for our V3 reference catalog (unmanaged path): Snowflake's V3 reader requires the row-lineage metadata columns (`METADATA$RL_ROW_ID`, `METADATA$RL_LAST_UPDATED_SEQUENCE_NUMBER`) physically present in parquet data files, even when our metadata.json sets `row-lineage: false` (which the V3 spec permits). We treat this as a Snowflake-side strictness gap to file rather than bend our reference catalog to match. See [engines/snowflake/README.md](engines/snowflake/README.md). |
| **Sedona 1.6.1 + Iceberg-Spark 1.7.1** | **L3** — 1 of 10 files | **L3** — prunes through struct fields | **L0** — `Cannot parse type string to primitive: geometry(OGC:CRS84)`. Sedona itself also can't *write* V3 geometry: `iceberg-spark-runtime` rejects Sedona's Geometry UDT (`UnsupportedOperationException: User-defined types are not supported`). Our V2 numeric bound encoding is bit-identical to Iceberg-Spark's. See [engines/sedona/README.md](engines/sedona/README.md). |
| **Databricks (DBSQL 2026.10)** | **L2** *via Snowflake federation* | **L2** *via Snowflake federation* | **L0** — but precisely: `GEOMETRY(SRID)`/`GEOGRAPHY(SRID)` **work in Delta**, while the Iceberg-compat writer rejects them (`DELTA_ICEBERG_WRITER_COMPAT_VIOLATION`, `IcebergWriterCompatV1`/`V3`) — geo types don't cross the Delta→Iceberg/UniForm boundary. Databricks "Iceberg" is Delta + an Iceberg-compat writer, not a native engine. Geo support in the Iceberg path is **likely coming soon** (not available as of 2026-05-26). **V2 update (2026-05-26):** Databricks reaches our V2 GeoIceberg data via `CREATE CONNECTION TYPE snowflake` + foreign catalog against a Snowflake-managed V2 table — schema correct (`geom_wkb` as `binary`), counts match Snowflake (10000/196/1000), and `st_geomfromwkb(geom_wkb)` parses the WKB into typed POINTs that `st_intersects` queries correctly. That's **query federation** (JDBC pushdown). The Iceberg-native **direct-from-GCS read does NOT engage** for Snowflake-on-GCP: Databricks's direct path only accepts the `gs://` scheme, but Snowflake-on-GCP vends its metadata location as `gcs://`, so it silently falls back to JDBC (every `EXPLAIN` → `SnowflakePlan`). **No generic Iceberg-REST connector — by design** (Databricks certifies per-partner connectors rather than accepting any spec-compliant IRC endpoint; demand-driven), so a self-hosted Polaris/Nessie/Lakekeeper isn't federatable. No static-`metadata.json` path either. See [engines/databricks/README.md](engines/databricks/README.md). |
| **Oracle ADB 26ai (23.26.2.2.0)** | **L0** | **L0** | **L0** — all fail with `ORA-20000: Failed to generate column list`. **Updated 2026-05-26:** ruled out *every* external variable — adding optional metrics (`column_sizes`/`value_counts`/`null_value_counts`) didn't help; **Snowflake's own Spark-lineage metadata fails identically**; and staging to **S3 with a working IAM credential** (Oracle `LIST_OBJECTS` succeeds) **still fails the same way**. So it's **not** storage (GCS vs S3), auth, producer, or metrics — the blocker is Oracle's Iceberg metadata reader / column-list generation itself for direct-`metadata.json` registration. See [engines/oracle/README.md](engines/oracle/README.md). |
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

Engines that *only* support catalog-mediated access need a named catalog
as a bridge. We did exactly this for Databricks: federated a
Snowflake-managed table into Unity Catalog via `CREATE CONNECTION TYPE
snowflake` and read our V2 GeoIceberg data through it (query federation).

### What actually governs interop: storage × catalog × auth

The L0–L4 ladder grades *engine × format*, but the harder-won lesson from
this testbed is that whether a read works **at all** is a product of three
orthogonal axes — and engines support uneven slices of the cube:

**1. Storage backend.** S3 is the lingua franca; GCS is second-class in
several engines. Databricks's direct-read path accepts
`s3/gs/abfss/r2/wasbs` but rejects Snowflake-on-GCP's `gcs://` metadata
scheme — so the *same table* on `s3://` would qualify for a direct read
and on `gcs://` silently falls back to JDBC. (Oracle is the exception that
proves it's not always storage: it fails on **both** GCS and S3 — there
the blocker is the engine's Iceberg reader itself.)

**2. Catalog mechanism** (how the table is announced):
- Static `metadata.json` on a bucket: DuckDB ✅, BigQuery ✅, Databricks ✗, Oracle ✗.
- Generic Iceberg REST: DuckDB ✅ (JWT→Horizon); Databricks ✗ — *no
  generic connector, by design*: it certifies per-partner connectors
  (`GLUE`/`HIVE_METASTORE`/`SNOWFLAKE`/`DATABRICKS`) rather than accepting
  any spec-compliant IRC endpoint, so conformance alone doesn't get you
  in — a self-hosted Polaris/Nessie/Lakekeeper can't be federated.
- Named catalog (Glue/HMS/Snowflake/Unity): the only thing Databricks federates.

**3. Auth mode** — "open vs behind auth" is its own axis:
- Public/anonymous; credential-vended (Snowflake needs an external volume
  *and* `storage.buckets.get`, public-ness doesn't exempt you); keyed vs
  keyless (Databricks Free Edition had Workload Identity disabled → SA-key
  only); OAuth/JWT (DuckDB→Horizon); and **long-lived vs temporary**
  (Oracle's AWS credential rejects STS session tokens with `ORA-20403` —
  needs long-lived IAM keys).

So "Snowflake-on-GCS" and "Snowflake-on-AWS" are genuinely different
cells: same engine, same format, different storage scheme → different
downstream behavior.

**The empty-credentials trap.** Many storage connectors have *no
first-class anonymous path* — the credential object is mandatory in the
API even for public data. To read a public bucket you often hand the
engine an **empty/placeholder credential** (DuckDB's `s3_access_key_id=''`,
Spark anonymous providers, an external volume with no secret) to route
into the anonymous code path. The data is open; the *engine* insists a
credential object exist. It's an API-shape artifact, not a security
requirement — and it trips people who reasonably assume "it's public, why
do I need a credential?"

### The reference catalog: a serverless, static Iceberg REST catalog

The constructive answer to all of the above is a **fully static, serverless
Iceberg REST catalog** — the "Portolan" pattern. Instead of running a Polaris
/ Nessie / Lakekeeper server, we pre-render the IRC read endpoints
(`/v1/config`, `/v1/{prefix}/namespaces`, `…/tables`, `…/tables/{table}`) as
plain JSON objects on the bucket. A generic IRC client consumes it as a real
REST catalog — with no server, no database, just CDN-friendly static files.

Generated by [`testbed/static_rest_catalog.py`](testbed/static_rest_catalog.py)
over all the fixtures (namespaces `v2` and `v3`), published at base URI:

```
https://storage.googleapis.com/cartobq-iceberg-geo-testbed/catalog
```

Consume it with a generic IRC client — DuckDB `ATTACH`, no auth:

```sql
INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs; LOAD spatial;
ATTACH 'geo' AS cat (
  TYPE iceberg,
  ENDPOINT 'https://storage.googleapis.com/cartobq-iceberg-geo-testbed/catalog',
  AUTHORIZATION_TYPE 'none'
);
SELECT COUNT(*) FROM cat.v2.v2_flat_columns;          -- 10000
SELECT ST_AsText(geom) FROM cat.v3.v3_geometry LIMIT 1;  -- typed POINT
```

Verified: DuckDB discovers all 5 tables across both namespaces and reads V2
(bbox pruning, WKB) and V3 (native geometry) through the catalog. This proves
generic IRC consumption works against an arbitrary, uncertified, serverless
endpoint — the open path that the big managed-warehouse federation features
(Databricks, and Glue's federation) don't take. `AUTHORIZATION_TYPE 'none'` is
needed because a static catalog has no OAuth token endpoint.

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
  `CONNECTION_TYPE_NOT_SUPPORTED` (Glue/Unity/Snowflake-Horizon only) —
  but `TYPE snowflake` *does* work: we federated a Snowflake-managed V2
  GeoIceberg table into Databricks Free Edition and read it (query
  federation; the direct-from-GCS path is blocked by a `gcs://`-vs-`gs://`
  scheme mismatch). See [engines/databricks/README.md](engines/databricks/README.md).

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
STATUS_V2.md                 # Living per-engine support for GeoIceberg V2
STATUS_V3.md                 # Living per-engine support for Iceberg V3 native geo
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
