# GeoIceberg V2 — engine support status

**Last verified: 2026-05-26.** Living document; PRs welcome.

This table tracks each engine's implementation status against the
capabilities defined in [SPEC.md](./SPEC.md) — the V2-era convention
that delivers file-level spatial pruning today using flat bbox columns
+ a WKB column.

Companion file: **[STATUS_V3.md](./STATUS_V3.md)** tracks engine
support for *Iceberg V3 native* geometry/geography types — the
eventual target this convention bridges to.

The README's L0–L4 matrix measures end-to-end query behavior across
fixtures; this document measures each *individual capability* the V2
spec calls out.

## Capability legend

The columns are the discrete behaviors a GeoIceberg V2 reader needs to
implement. The first four are required to claim conformance; the
fifth is an optional engine optimization the spec recommends.

| # | Capability | Required? | What it means |
|---|---|---|---|
| **R1** | Static metadata read | yes\* | Reader can register a table by pointing at `metadata.json` on cloud storage. \*Not all engines support this path; engines that only support catalog-mediated access are marked **catalog-only**. |
| **R2** | bbox-col file pruning | **yes** | Engine applies the standard overlap predicate (`xmin ≤ q.xmax AND xmax ≥ q.xmin AND ymin ≤ q.ymax AND ymax ≥ q.ymin`) to the manifest `lower_bound`/`upper_bound` on the bbox columns. The core promise of the convention. |
| **R3** | WKB column readback | **yes** | Engine can decode the `geom_wkb` BINARY column via `ST_GeomFromWKB()` (or equivalent) into a usable geometry. |
| **R4** | `geo` property visible | should | The `geo` table property is surfaced via standard metadata queries (`SHOW TBLPROPERTIES`, `INFORMATION_SCHEMA`, etc.) so tooling can detect GeoIceberg V2 tables. |
| **O1** | Auto-derive bbox from `ST_Intersects` | optional | Engine synthesizes the bbox-col predicate when only `ST_Intersects(geom_wkb, envelope)` is present. The current GeoParquet 1.1 ecosystem doesn't do this either — when an engine implements it, both formats benefit transparently. |

Cell values:

- ✅ — verified working in this testbed
- ⚠️ — works with caveats (see notes)
- ❌ — not supported
- ❓ — not yet tested
- n/a — capability doesn't apply to this engine's access pattern

## Engine support table

| Engine / version | R1 static metadata | R2 bbox-col pruning | R3 WKB readback | R4 `geo` property visible | O1 auto-derive from `ST_Intersects` |
|---|---|---|---|---|---|
| **DuckDB 1.5.3** | ✅ | ✅ — 1/10 files on California probe | ✅ — `ST_GeomFromWKB(geom_wkb)` returns POINT geometries | ❓ — `iceberg_scan()` doesn't expose table properties by default | ❌ — confirmed via Q3 in the testbed |
| **BigQuery / BigLake** (2026-05) | ✅ — `CREATE EXTERNAL TABLE … OPTIONS(format='ICEBERG', uris=[…])` | ✅ — `total_bytes_processed` matches single-file scan | ✅ — `ST_GEOGFROMWKB(geom_wkb)` (note: returns GEOGRAPHY, not GEOMETRY) | ❓ | ❌ — same as GeoParquet's current state |
| **Sedona 1.6.1 + Iceberg-Spark 1.7.1** | ✅ — via Spark's `read.format('iceberg').load(metadata_path)` | ✅ — distinct `input_file_name()` = 1 | ✅ — `ST_GeomFromWKB(geom_wkb)` returns Sedona Geometry | ❓ | ❌ |
| **Snowflake 10.19.100 (GCP_EUROPE_WEST2)** | catalog-only — requires `EXTERNAL VOLUME` + `CATALOG INTEGRATION` with `CATALOG_SOURCE = OBJECT_STORE`. *Key gotcha:* the GCS service account needs `storage.buckets.get` (via `roles/storage.legacyBucketReader`) in addition to `objectAdmin`, otherwise `CREATE ICEBERG TABLE` fails with the misleading `091369: Query needs to be retried to setup external volume`. `SYSTEM$VERIFY_EXTERNAL_VOLUME` doesn't catch this, and `ICEBERG_ACCESS_ERRORS` doesn't log it. Snowflake support confirmed; documented in [engines/snowflake/README.md](engines/snowflake/README.md). | ✅ — verified L3 on all three V2 fixtures (`v2_flat_columns`, `v2_bbox_struct`, `v2_geo_convention`); `bytes_scanned=0` because Snowflake answers `COUNT(*)` with bbox predicate from the manifest's `record_count` directly (no parquet read needed) | ✅ — `geom_wkb` exposes as BINARY; `TO_GEOMETRY(geom_wkb)` materializes points | ❓ — needs explicit `SHOW TBLPROPERTIES` test | ❓ |
| **Databricks (DBSQL 2026.10)** | catalog-only — no generic Iceberg REST or static-metadata path. ✅ **reachable via `CREATE CONNECTION TYPE snowflake`** against a Snowflake-managed V2 table (2026-05-26); query federation only — the direct-from-GCS read falls back to JDBC because Databricks rejects Snowflake-on-GCP's `gcs://` metadata scheme (accepts only `gs://`) | ✅ — federated bbox predicate returns 196, matches Snowflake | ✅ — `st_geomfromwkb(geom_wkb)` parses WKB to typed POINTs; `st_intersects` correct (=1000) | ❓ | ❓ |
| **Oracle ADB 26ai** | ⚠️ — `DBMS_CLOUD.CREATE_EXTERNAL_TABLE` syntax exists and works for some manifests, but rejects pyiceberg-emitted manifests with `ORA-20000: Iceberg parameter error`. Reader is stricter than the spec; likely fixable by also populating optional manifest stats (`column_sizes`, `value_counts`, `null_value_counts`). | ❓ — blocked by R1 today | ❓ | ❓ | ❓ |
| **Apache Polaris** (reference REST catalog) | ✅ — registers via `POST /api/catalog/v1/{cat}/namespaces/{ns}/register` with a `metadata-location` pointing at our GCS metadata.json | n/a — Polaris is a catalog, not a query engine | n/a | n/a — Polaris exposes the property to client engines | n/a |
| **PyIceberg 0.11.1** | ✅ — `pyiceberg.io.pyarrow.PyArrowFileIO` reads V2 metadata | ❓ — needs explicit row-filter test | ✅ — returns the WKB column as Arrow `binary` | ❓ | n/a (library, not a query planner) |

\*Engines marked **catalog-only** in R1 may still implement R2–R4
correctly once tables are registered through their supported catalog
path. The `n/a` cells in this table are testbed methodology gaps, not
engine capability gaps.

## What each cell would need to flip

### To turn ❓ → ✅ on **R4 (`geo` property visible)**

Run an engine-specific check that the `geo` table property is queryable:

| Engine | Probe |
|---|---|
| DuckDB | `SELECT properties FROM iceberg_metadata('<metadata.json>');` (function may need adding to the runner) |
| BigQuery | `SELECT * FROM \`<dataset>\`.INFORMATION_SCHEMA.TABLE_OPTIONS WHERE table_name='…';` |
| Sedona / Spark | `SHOW TBLPROPERTIES <catalog.namespace.table>;` |
| Snowflake | `SHOW TBLPROPERTIES <table>;` after registration |

### To turn ❌ → ✅ on **O1 (auto-derive bbox)**

This is an engine-internals change, not a config knob. The optimization
would inspect the `geo` table property at query plan time, detect a
spatial predicate (`ST_Intersects`, `ST_Within`, etc.) on the geometry
column, derive the envelope's `xmin/ymin/xmax/ymax`, and add the bbox-
col overlap predicate to the file-prune step. As of 2026-05, no engine
we tested implements this — the same is true for GeoParquet 1.1's
covering bbox struct.

PR targets when filing:

- **DuckDB**: [`duckdb/duckdb-iceberg`](https://github.com/duckdb/duckdb-iceberg)
- **BigQuery**: feature request via BigLake support
- **Sedona / Iceberg-Spark**: [`apache/iceberg`](https://github.com/apache/iceberg)
  Spark connector

## What's in the testbed today vs missing

What the runners measure right now:

- R1, R2, R3 for DuckDB, BigQuery, Sedona (the engines with static-metadata access).
- R1 for Polaris.
- Negative findings for V3 geometry at L0 on all engines (separate matrix).

What's not yet automated:

- R4 (`geo` property visibility) — needs a runner pass that queries each engine's metadata system.
- O1 — needs explicit checking that `ST_Intersects(geom_wkb, env)` *alone* triggers file pruning. We have the Q3 probe in `testbed/v2_geo_convention.py`'s comments but it isn't wired into the engine runners yet.

PRs that add these to the engine runners would automatically keep this
status table honest as engines evolve.

## How to update this document

This file is intended to be updated whenever:

- A new engine version ships with a relevant change.
- An engine PR lands that flips a cell.
- A new engine joins the testbed.

The matrix in [README.md](./README.md) gets refreshed by running the
engine probes; this status table gets refreshed by hand when capabilities
are verified. Keep the "Last verified" date at the top of the file
current; add a one-line changelog at the bottom for each material
change.

## Changelog

- **2026-05-26** — Initial publication. DuckDB / BigQuery / Sedona
  measured at R1+R2+R3. Snowflake/Databricks/Oracle have known
  blockers documented per engine. Polaris confirmed as spec validator.
  O1 unimplemented in any engine we tested.
- **2026-05-26 (later)** — Snowflake unblocked. Snowflake support
  identified the missing `storage.buckets.get` IAM permission as the
  cause of the long-standing 091369 error. After granting
  `roles/storage.legacyBucketReader` to the GCS service account,
  all three V2 fixtures register cleanly and hit L3 file pruning
  (Snowflake actually answers `COUNT(*)` queries from manifest
  `record_count` alone — even stronger than file pruning).
