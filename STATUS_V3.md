# Iceberg V3 native geometry — engine support status

**Last verified: 2026-10-09** for DuckDB (1.5.6), PyIceberg and GeoParquet 2.0
conformance of the fixtures; BigQuery 2026-09-21. The Snowflake, Databricks, Oracle and
Spark rows were last run 2026-05-28; each row states its own date and evidence.
Living document; PRs welcome.

Part of the **geo track**. This table tracks each engine's implementation status
against the native V3 geometry/geography types defined in the
[Apache Iceberg spec](https://iceberg.apache.org/spec/#geometry).

Companion files:
- **[STATUS_V2.md](./STATUS_V2.md)** — the *GeoIceberg V2 convention*, the
  workaround that delivers file-level spatial pruning on engines that don't yet
  support V3 native geometry. The recommended migration path while this table
  stays mostly red.
- **[STATUS_CATALOG.md](./STATUS_CATALOG.md)** — the *catalog track*: how engines
  reach a table (catalog/auth/storage), independent of geo. The REST-catalog
  attach mechanics referenced below (DuckDB↔Horizon, etc.) are part of that
  story.

## The reference catalog

The testbed ships **three V3 geo fixtures**:

- **`v3_geometry`** — spec-minimal (`row-lineage: false`). The canonical
  reference; readers should accept this if they support V3 at all.
- **`v3_geometry_lineage`** — `row-lineage: true` + `_row_id` /
  `_last_updated_sequence_number` columns populated in each data file
  at the Iceberg V3 spec field IDs (`2147483545` / `2147483544`). For
  testing stricter readers that require lineage columns be present
  regardless of the metadata flag.
- **`v3_geography`** — the `geography` twin of `v3_geometry`: Iceberg
  type token `geography`, parquet logical type
  `Geography(crs=, algorithm=spherical)`. Same regions and seeds, so the
  WKB payloads are **byte-identical** to `v3_geometry` (verified across
  all 10 files) — a reader that handles one and not the other differs on
  the type annotation alone, not on the data. The omitted type-token
  parameters default to `OGC:CRS84` / `spherical` per spec, matching what
  the parquet annotation declares.

The shared properties of these fixtures:

- `format-version: 3` with `row-lineage: false` (spec-permitted off)
- Schema: `id: string` plus the geo column (`geom: geometry`, or
  `geog: geography` for `v3_geography`). No CRS in the type token —
  CRS info lives in the parquet column's logical type.
- `next-row-id`, `last-column-id`, `statistics: []`,
  `partition-statistics: []` populated as the V3 spec expects.
- Manifest avro is real V3 (subclassed pyiceberg's V2 writers since
  the upstream library's V3 writer is incomplete; see
  `testbed/_static_catalog.py`). Includes `first_row_id` on data
  files, `first_row_id` on the manifest-list entry, and the
  `iceberg.schema` metadata key Snowflake-managed V3 emits.
- **Parquet data files use the native parquet-format 2.11 logical
  types** via `geoarrow-pyarrow` — `Geometry(crs=)`, or
  `Geography(crs=, algorithm=spherical)` for `v3_geography` — with
  WKB-encoded point payloads. Same column-level encoding Snowflake's own
  managed V3 writer produces.
- **…and are conformant GeoParquet 2.0 files.** [GeoParquet 2.0.0-rc.1](https://github.com/opengeospatial/geoparquet/releases/tag/v2.0.0-rc.1)
  (2026-07-19) makes those native types its foundation but still requires
  the `geo` footer key for conformance; a native-types-only file is
  "parquet-geo-only", readable by 2.0 readers but not conformant. Since
  2026-09-22 each data file carries `geo` metadata (`version: "2.0.0"`,
  `encoding: "WKB"`, `geometry_types: ["Point"]`, per-file `bbox`, and
  `edges: "spherical"` on `v3_geography`; `crs` omitted so the OGC:CRS84
  default matches the absent Parquet `crs`). `gpio check all` passes.
  Iceberg readers ignore this footer key, so nothing changes on the
  Iceberg side.
  Because the data files carry both layers on their own, they are also
  usable as **bare GeoParquet 2.0 conformance files**, with no Iceberg
  metadata involved:
  `https://storage.googleapis.com/cartobq-iceberg-geo-testbed/{v3_geometry,v3_geography}/data/<region>.parquet`
- Per-file geometry bounds in the `packed_xy_le` encoding (16 bytes:
  little-endian X, little-endian Y) — confirmed against Snowflake's
  own bound bytes byte-for-byte.

Published at `gs://cartobq-iceberg-geo-testbed/{v3_geometry,v3_geography}/`
(public). This is what V3 readers should be tested against.

A reader that rejects any of these fixtures has an *engine-side* gap to
file against the engine vendor, not against this testbed.

### `geography`: the gap is the Iceberg type layer, not Parquet

The engine table below tracks `geometry` only. The `v3_geography` twin
buys a sharper diagnosis, because it isolates *which layer* of an engine
lacks geography support — the parquet logical type or the Iceberg type
token. First result, DuckDB 1.5.3 (2026-08-05); re-verified unchanged on
DuckDB 1.5.6 (2026-10-09):

| Path | `v3_geometry` | `v3_geography` |
|---|---|---|
| `read_parquet(…/data/*.parquet)` | ✅ column arrives as native `GEOMETRY` | ✅ **also** native `GEOMETRY` — `Geography(crs=, algorithm=spherical)` is recognized, no WKB decode needed; `ST_Intersects` over the California window returns all 1000 points and correctly excludes the other 9 regions |
| `iceberg_scan(…/metadata/v1.metadata.json)` | ✅ `COUNT(*) = 10000` | ❌ `Not implemented Error: Geography support` |

Same bytes on disk in both columns of that table — so DuckDB's geography
gap lives entirely in its Iceberg type mapping. Its Parquet reader already
does the right thing.

One writer-side asymmetry to know about: pyarrow 25.0.1 emits row-group
`GeospatialStatistics` (the parquet-format 2.11 bbox) for the `GEOMETRY`
column but **none** for the `GEOGRAPHY` column. So a bare-Parquet reader
gets row-group bbox pruning on `v3_geometry` and not on `v3_geography`;
the Iceberg manifest bounds are unaffected (we write those ourselves). That's a much more actionable bug report than
"geography doesn't work," and it's only visible because the two fixtures
are byte-identical apart from the annotation.

### Cross-engine V3 interop verified

DuckDB reads **Snowflake's own managed V3 table** at exactly the same
level it reads our hand-written V3 fixture — same COUNT/SELECT/
ST_AsText results; on 1.5.3 the same `ST_Intersects` bound-deserializer
crash, and on 1.5.6 the same manifest geometry-bound pruning with
`ST_Intersects_Extent` (1/10 files on ours, 2/7 on Snowflake's — its
files aren't region-aligned). That's strong evidence that:

- Our catalog's V3 metadata + manifest avro + parquet structure is
  equivalent to Snowflake's for the parts DuckDB exercises.
- DuckDB's (former) bound-deserializer gap was engine-side (same line
  of code failed whether the V3 table was written by us or by
  Snowflake). We filed it as [#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002); [PR #1030](https://github.com/duckdb/duckdb-iceberg/pull/1030)
  decodes the `packed_xy_le` bounds from both writers identically.

The Snowflake-managed V3 table lives at
`gs://cartobq-iceberg-geo-testbed-eu/managed-v3-geo.MLyhYkeQ/` and is
publicly readable; we use it as a second cross-engine reference.

### DuckDB ↔ Snowflake Horizon REST catalog — ✅ verified

DuckDB attaches Snowflake's Iceberg REST catalog (Horizon) using
JWT-key-pair OAuth, discovers the managed V3 GEOMETRY table via the
catalog API, and reads it at the same level (L2) as via direct GCS
URL. The catalog-attach interop mechanism works end-to-end.

The setup script is at `engines/snowflake/_horizon_jwt.py`. Steps:

1. Generate RSA 2048 keypair locally (one-time; written to
   `~/.config/iceberg-geo-testbed/horizon-keys/`).
2. `ALTER USER JATORRETESTBED SET RSA_PUBLIC_KEY = '<base64>'`.
3. Sign a JWT with claims `iss = <ACCOUNT>.<USER>.SHA256:<fp>`,
   `sub = <ACCOUNT>.<USER>`, `iat`/`exp`.
4. POST to `…/polaris/api/catalog/v1/oauth/tokens` with
   `grant_type=client_credentials`, `scope=session:role:ACCOUNTADMIN`,
   `client_secret=<JWT>` → returns OAuth access token.
5. Pass to DuckDB:

   ```sql
   CREATE SECRET horizon (TYPE iceberg, TOKEN '<access_token>');
   ATTACH 'TESTBED' AS sf
     (TYPE iceberg, SECRET horizon,
      ENDPOINT 'https://<account>.snowflakecomputing.com/polaris/api/catalog');
   SELECT ST_AsText(GEOM) FROM sf.PUBLIC2.MANAGED_V3_GEO LIMIT 3;
   ```

Verified results (same as direct GCS URL path):
- `SHOW ALL TABLES` discovers `MANAGED_V3_GEO`
- `DESCRIBE` returns `ID varchar`, `GEOM geometry('ogc:crs84')`
- `COUNT(*) = 10000`
- `SELECT ST_AsText(GEOM)` materializes proper POINT geometries
- `WHERE ST_Intersects(GEOM, …)` hits the same bound-deser crash (on
  1.5.3; the Horizon path was not re-run on 1.5.6 — needs Snowflake creds)

Two ways DuckDB can reach Snowflake's V3 data, both at L2. Either
counts as cross-engine V3 interop.

Notably, the lineage fixture didn't flip any engine result during our
testing:

- **DuckDB** treats the extra lineage columns as metadata-only
  (respects the schema in `metadata.json` which doesn't list them),
  so both fixtures behave identically (L2).
- **BigQuery** rejects at the geometry type token long before any
  lineage check.
- **Snowflake's unmanaged reader** still rejects with "incomplete
  state" — and we proved this rejection is *not* about lineage
  columns or naming. We tested a third one-off variant
  (`v3_geometry_snowflake_compat`) using Snowflake's exact
  internal column names (`METADATA$RL_*`) at Snowflake's exact
  internal field IDs (2147483540 / 2147483539). Same rejection.
  Combined with the fact that our metadata.json + manifest avro
  + parquet schemas now match Snowflake's own output byte-for-byte,
  this very strongly suggests **Snowflake's V3 unmanaged read path
  is not yet generally functional** — they can write V3 managed and
  read their own V3 back, but consuming an externally-produced V3
  table doesn't work regardless of structure. Worth a Snowflake
  support ticket asking: "what's required for the V3 unmanaged
  reader to accept an external V3 fixture?"

## Capability legend

V3 native geometry support breaks down into four read-side
capabilities plus one write-side capability. Each is independent; an
engine can ship them in any order.

| # | Capability | What it means |
|---|---|---|
| **N1** | Type token recognized | Engine parses `geometry(<crs>)` and `geography(<crs>, <algo>)` in `metadata.json` without erroring; the table registers. |
| **N2** | Column readback | `SELECT geom FROM t` materializes actual geometry values (typed, not raw blobs). |
| **N3** | Spatial predicate correctness | `WHERE ST_Intersects(geom, envelope)` returns the right rows, regardless of pruning. |
| **N4** | Manifest geometry-bound pruning | The V3 manifest's per-file `lower_bound`/`upper_bound` on the geometry column is used to skip non-overlapping files. This is the "headline feature" of V3 vs V2. |
| **W1** | Write conformant V3 geometry tables | Engine can produce a V3 table with a `geometry(<crs>)` column, populated manifest geometry bounds, and a readable layout for other V3 readers. |

Cell values:

- ✅ — verified working in this testbed
- ⚠️ — works with caveats (see notes)
- ❌ — not supported (verified failure mode)
- 📋 — claimed by the vendor but not yet verified in this testbed
- ❓ — not yet tested
- n/a — capability doesn't apply to this engine's access pattern

## Engine support table

| Engine / version | N1 type recognized | N2 column readback | N3 predicate correct | N4 manifest geometry-bound pruning | W1 write V3 tables |
|---|---|---|---|---|---|
| **DuckDB 1.5.6** (iceberg extension `890b78a9c`, spatial `04270fe`; 2026-10-09) | ✅ — schema parses, `COUNT(*)` works (unchanged since 1.5.3). `geography` is still rejected: `Not implemented Error: Geography support`. | ✅ — typed `geometry` materializes; `ST_AsText(geom)` returns WKT cleanly. **Cross-verified against both our hand-written V3 fixture AND Snowflake's own managed V3 table.** (The earlier "BLOB→GEOMETRY cast missing" finding was our parquet writing geom as plain BINARY; GeoParquet 2.0 native `Geometry(crs=)` typing fixed it.) | ✅ **on the stock release** — no local build needed anymore. `WHERE ST_Intersects(geom, ST_MakeEnvelope(-125, 32, -115, 42))` returns 1000 on our fixture and on Snowflake's managed V3. The 1.5.3 crash ([#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002)) was closed upstream on 2026-09-01 after [PR #1013](https://github.com/duckdb/duckdb-iceberg/pull/1013) (stop crashing) and [PR #1030](https://github.com/duckdb/duckdb-iceberg/pull/1030) (decode the bounds). | ✅ **with a bbox predicate.** [PR #1030](https://github.com/duckdb/duckdb-iceberg/pull/1030) (merged 2026-06-10, in the stock extension) decodes the `packed_xy_le` bounds into a `GeometryStats` extent and prunes via `GeometryStats::CheckZonemap`. `ST_Intersects_Extent(geom, env)` or `geom && env` → `Total Files Read: 1` of 10 on `v3_geometry` and on `v3_geometry_lineage`; 2/7 on Snowflake's managed V3 (its files aren't region-aligned). **Caveat:** plain `ST_Intersects` is *not* rewritten into a bbox pre-filter (no such optimizer rule in duckdb-spatial 1.5.6), so it full-scans (10/10, 7/7). `ST_Intersects_Extent(...) AND ST_Intersects(...)` gives pruning **and** exact semantics (verified: 1/10 files, 1000 rows). | ❓ — PR #1030 also writes geometry bounds on `INSERT` into a catalog-attached table; not exercised in this testbed |
| **BigQuery / BigLake** (2026-05; re-verified 2026-09-21) | ❌ on geometry — `Unknown Iceberg type "geometry(OGC:CRS84)"` at `CREATE EXTERNAL TABLE` (2026-05); still ❌ on 2026-09-21 with the bare tokens: `Unknown Iceberg type "geometry"` on `v3_geometry`, `Unknown Iceberg type "geography"` on `v3_geography`. **V3 reader itself works**: `CREATE EXTERNAL TABLE … OPTIONS(format='ICEBERG', uris=['…v3_minimal/metadata/v1.metadata.json'])` against a V3-minimal fixture (no geometry, just `id` STRING + `n` INT) returns `COUNT(*) = 10000`. So the gap is geometry-specific, not V3-format. | ❌ — blocked by N1 | ❌ — blocked by N1 | ❌ — blocked by N1 | ❌ — `GEOGRAPHY` type also explicitly unsupported per [icebergmatrix.org](https://icebergmatrix.org/) |
| **Sedona 1.6.1 + Iceberg-Spark 1.7.1** | ❌ — `Cannot parse type string to primitive: geometry(OGC:CRS84)` on `spark.read.format('iceberg').load(...)` | ❌ — blocked by N1 | ❌ — blocked by N1 | ❌ — blocked by N1 | ❌ — `iceberg-spark-runtime` rejects Sedona's Geometry UDT: `java.lang.UnsupportedOperationException: User-defined types are not supported at SparkTypeVisitor.visit`. Even the reference V3 toolchain can't write native geometry today. |
| **Dataproc Serverless 2.3** (Spark 3.5.3, GCP) | ❌ on geometry — bundled `iceberg-spark-runtime` raises `UnsupportedOperationException: Cannot convert unknown type to Spark: geometry`. **V3 reader works**: `spark.read.format("iceberg").load(…v3_minimal…)` returns 10000 rows with a clean `id:string, n:int` schema. So Dataproc has shipped V3; the gap is geometry-specific. | ❌ — blocked by N1 | ❌ — blocked by N1 | ❌ — blocked by N1 | ❓ |
| **EMR Serverless 7.13** (Spark 3.5.6-amzn-2, AWS) | ❌ on geometry — same `UnsupportedOperationException: Cannot convert unknown type to Spark: geometry`. **V3 reader works**: same `spark.read.format("iceberg").load(…v3_minimal…)` returns 10000 rows. icebergmatrix.org lists "EMR (8.0 Spark): Full" for V3 Geometry, but `emr-8.x` doesn't exist in `aws emr list-release-labels` (latest is `emr-7.13.0`) — that cell is forward-looking; the testable EMR today is L0 on geometry but does have a working V3 reader for other types. | ❌ — blocked by N1 | ❌ — blocked by N1 | ❌ — blocked by N1 | ❓ |
| **Snowflake (GA May 2026)** | ✅ for both managed *and* unmanaged paths. Managed: `ICEBERG_VERSION=3` required (the default for new tables is V2; the error `Unsupported data type 'GEOMETRY'` doesn't hint at the opt-in). Unmanaged: `CREATE OR REPLACE ICEBERG TABLE` over an external metadata.json + external volume works as long as the writer is V3-spec-compliant (see W1 note). | ✅ — `SELECT geom` materializes as `GEOMETRY(4326)` on both paths. | ✅ — `WHERE ST_INTERSECTS(geom, envelope)` returns the right rows (using `TO_GEOMETRY(wkt, 4326)` or `ST_GEOMFROMWKT(wkt, 4326)` to match the column SRID; a bare `TO_GEOMETRY(wkt)` fails with `Incompatible SRID: 4326 and 0`). | ✅ — `bytes_scanned` for a spatial predicate is ~1/10 of a full GEOM-column scan on a 10-file fixture (verified on both managed and our externally-written V3): manifest geometry-bound pruning fires on both paths. | ✅ — full write path works with `CATALOG='SNOWFLAKE'` (managed). Writes Parquet-native `Geometry` columns (GeoParquet 2.0) + V3 manifest avro with `first_row_id` / geometry bounds populated using `packed_xy_le` (16-byte LE-double-X, LE-double-Y) — empirically matches our testbed's encoding. **For externally-written V3 to be readable, the writer must (a) put `first-row-id` and `added-rows` in the snapshot block — V3 spec compliance; without them Snowflake fails with `incomplete state`. For the spatial *predicate* path to work, the manifest also needs (b) `value_counts` + `null_value_counts` populated and (c) `lower_bounds` + `upper_bounds` populated for the ID column too (not just geom); without these Snowflake's V3 manifest-bound pruner trips on a variant-cast over the `packed_xy_le` geom bound and the predicate fails with `Failed to cast variant value "..." to REAL`.** None of this requires Snowflake-internal lineage cols (`METADATA$RL_*`), uppercase column names, or `last-column-id` bumps — verified by bisection. `testbed/v3_geometry.py` now emits all of the above and Snowflake reads it end-to-end at L3. |
| **Databricks (DBSQL 2026.10)** | ❌ in Iceberg — but precisely (verified 2026-05-26): `GEOMETRY(SRID)`/`GEOGRAPHY(SRID)` **work in Delta**, while the Iceberg-compat writer rejects them (`DELTA_ICEBERG_WRITER_COMPAT_VIOLATION`, `IcebergWriterCompatV1`/`V3`). Databricks "Iceberg" = Delta + an Iceberg-compat writer, so geo stops at that boundary. Geo-in-Iceberg is **likely coming soon** (not available 2026-05-26); re-test periodically. | ❌ | ❌ | ❌ | ❌ |
| **Oracle ADB 26ai (23.26.2.2.0)** | ❌ — blocked upstream of V3: Oracle can't read *any* of our Iceberg tables (V2 or V3), including Snowflake's own Spark-lineage output — `ORA-20000: Failed to generate column list` (see V2 status, updated 2026-05-26). Ruled out storage (fails on both GCS-public and S3-credentialed), producer, and metrics — it's Oracle's Iceberg reader itself. Can't isolate the V3 geometry question until Oracle reads our Iceberg at all. | ❌ | ❌ | ❌ | ❓ — not in icebergmatrix.org's coverage |
| **Apache Polaris** (reference REST catalog) | ✅ — registers V3 tables via `POST .../register` once metadata includes the required `next-row-id` and `row-lineage` fields (caught a real pyiceberg 0.11.1 gap we patched in `testbed/_static_catalog.py`) | n/a — Polaris is a catalog, not a query engine | n/a | n/a | n/a |
| **PyIceberg 0.12.0** (2026-09-01) | ✅ — `GeometryType` / `GeographyType` landed ([#2859](https://github.com/apache/iceberg-python/pull/2859)); `StaticTable.from_metadata` parses all three V3 fixtures (`geom: GeometryType()`, `geog: GeographyType()`) and plans their 10 files. V3 tracking issue [#1818](https://github.com/apache/iceberg-python/issues/1818) is still open. | ❌ — `scan().to_arrow()` fails with `UnsupportedPyArrowTypeException: Column 'geom' has an unsupported type: extension<geoarrow.wkb<WkbType>>` — the GeoParquet-2.0 files carry a `geoarrow.wkb` Arrow extension type (registered as soon as `geoarrow-pyarrow` is importable) that pyiceberg's Arrow→Iceberg schema visitor doesn't accept. `v3_minimal` (no geometry) reads fine. | n/a (library) | n/a | ❌ — `write_manifest()` / `write_manifest_list()` still reject `format_version=3`; the V3 writer subclasses in `testbed/_static_catalog.py` remain necessary |
| **OSS Spark 4.1 / Flink 2.2** (not in this testbed) | ❓ | ❓ | ❓ | ❓ | ❓ — per icebergmatrix.org: *"V3 geometry type support is not yet documented"* |

## What this picture tells you

As of mid-2026:

- **Snowflake delivers N1–N4 + W1 end-to-end on both the managed *and*
  the unmanaged read paths.** With `ICEBERG_VERSION = 3` opted in for
  managed tables, and with a V3-spec-compliant writer for unmanaged
  tables — `testbed/v3_geometry.py` is sufficient as of 2026-05-28 —
  Snowflake accepts `GEOMETRY` columns, materializes them via SQL,
  applies spatial predicates correctly, and prunes on manifest geometry
  bounds at file granularity on both paths. What initially looked like
  a "managed-only" capability turned out to be a V3 spec-compliance gap
  in our writer: Snowflake requires the V3 snapshot's `first-row-id` /
  `added-rows` and, for spatial predicates, populated `value_counts` +
  `null_value_counts` + ID-column bounds in the manifest. Bisection
  ruled out everything else (no Snowflake-internal lineage cols, no
  uppercase columns, no `last-column-id` bumps).
- **DuckDB 1.5.6 delivers N1–N4 on the stock release.** N2 came when
  we upgraded our V3 parquet files to the native `Geometry(crs=)`
  logical type (GeoParquet 2.0) — a fix-the-catalog issue, not a
  fix-the-engine one. N3 landed with [PR #1013](https://github.com/duckdb/duckdb-iceberg/pull/1013) (stop
  crashing) and N4 with [PR #1030](https://github.com/duckdb/duckdb-iceberg/pull/1030) (decode the
  `packed_xy_le` bounds), both in the stock extension. The remaining
  rough edge is ergonomic: pruning fires only on bbox predicates
  (`ST_Intersects_Extent`, `&&`); a plain `ST_Intersects` is correct
  but full-scans until duckdb-spatial derives a bbox pre-filter from
  it. `geography` is still unmapped in `iceberg_scan`.
- **Upstream Spark has shipped geometry.** [Apache Iceberg
  1.12.0](https://github.com/apache/iceberg/releases/tag/apache-iceberg-1.12.0)
  (2026-09-30) includes Spark 4.1 geometry type mapping and Parquet
  read/write ([#16851](https://github.com/apache/iceberg/pull/16851),
  [#17073](https://github.com/apache/iceberg/pull/17073)) and Parquet
  geometry bounding-box metrics
  ([#17161](https://github.com/apache/iceberg/pull/17161)). The Sedona /
  Dataproc / EMR rows above were measured on 1.11.0 and predate it;
  re-test them on an `iceberg-spark-runtime` 1.12.0.
- **Other engines** (BigQuery, Sedona/Iceberg-Spark, Databricks)
  reject the V3 geometry type at parse, before reaching N2.
- **W1 outside of Snowflake**: Sedona/Iceberg-Spark — the supposed
  reference implementation — has `iceberg-spark-runtime` lacking the
  UDT→IcebergGeometryType mapper. pyiceberg V3 writes are incomplete
  (tracked at #1818). Snowflake-managed is the only working V3
  geometry writer we found.

This is the empirical reason [**STATUS_V2.md**](./STATUS_V2.md) and
the [**GeoIceberg V2 spec**](./SPEC.md) exist. The V3 story now ships
end-to-end in two engines (Snowflake, and DuckDB 1.5.6). Until it
spreads to the rest, the V2 convention bridges the gap.

## What each cell would need to flip

### **N1 type recognized**

The most "fixable" cell. The change is at the metadata-parser layer:
recognize `geometry(<crs>)` / `geography(<crs>, <algo>)` as valid type
tokens. Engines vary in how strict this is:

- **BigQuery / Databricks / Sedona**: parser-level rejection — these
  need a code change in their Iceberg-V3 reader to accept the type.
- **Snowflake**: claims this is shipped in preview. Verifying requires
  unblocking their `091369` bug.

### **N2 column readback**

DuckDB: **done.** The apparent missing `BLOB → GEOMETRY` cast was our
parquet files declaring plain `BINARY`; with GeoParquet-2.0 native
`Geometry(crs=)` typing DuckDB reads the column directly. The
[issue we filed](https://github.com/duckdb/duckdb-iceberg/issues/1002) is closed.

### **N3 spatial predicate correctness**

DuckDB: **shipped; verified on 1.5.6** ([PR #1013](https://github.com/duckdb/duckdb-iceberg/pull/1013) then [PR #1030](https://github.com/duckdb/duckdb-iceberg/pull/1030)).
Spatial predicates evaluate correctly on the stock release — verified
end-to-end on the hand fixture and on Snowflake-managed V3.

### **N4 manifest geometry-bound pruning**

The headline V3 feature. DuckDB: **shipped; verified on 1.5.6** via
[PR #1030](https://github.com/duckdb/duckdb-iceberg/pull/1030) — the V3 `packed_xy_le` encoding (16-byte LE-double
X,Y pair — see [docs/encoding.md](docs/encoding.md)) is decoded into a
`GeometryStats` extent and evaluated by `GeometryStats::CheckZonemap`.
What's left is ergonomics: the zonemap check whitelists the bbox-only
predicates (`&&`, `ST_Intersects_Extent`), and duckdb-spatial has no
optimizer rule that derives one from `ST_Intersects`, so a plain
`ST_Intersects` still reads every file. Two follow-ups worth filing:
an `ST_Intersects → bbox pre-filter` rewrite (duckdb-spatial), and the
`geography` type mapping in `iceberg_scan` (duckdb-iceberg).

### **W1 write V3 tables**

The Spark/Iceberg path requires the `iceberg-spark-runtime` to gain a
UDT-to-IcebergGeometryType mapper. The Sedona team is best-positioned
to drive this since they're already the Geometry-UDT producer; the
[apache/iceberg](https://github.com/apache/iceberg) Spark connector
is the PR target.

For other engines (DuckDB, BigQuery, Databricks) the write path is
contingent on the engine acquiring native geometry types in its
storage layer, which is a much larger undertaking.

## How to update this document

Same protocol as STATUS_V2.md: update "Last verified" at the top,
flip cells as engines ship the capability, and add a one-line entry
to the changelog.

## Changelog

- **2026-05-26** — Initial publication. DuckDB N1 confirmed; N2–N4
  + W1 unimplemented on every engine we could test. Snowflake's
  claimed V3 support remains unverified pending account-side
  fix. Polaris confirmed it accepts our V3 metadata once `next-row-id`
  is populated.
- **2026-05-26 (later)** — Snowflake account-side bug resolved
  (missing `storage.buckets.get` IAM permission per Snowflake
  support). V2 fixtures now all work at L3 on Snowflake.
- **2026-05-26 (later still)** — Reclassified Snowflake V3 cells
  from `📋` (claimed but unverified) to `❓` (untested). The
  rejection of our V3 fixture is **our spec-noncompliance** (V2
  manifest avro paired with V3 metadata.json), not a Snowflake
  capability gap. To actually test Snowflake's V3 geometry support
  we'd need to drive Snowflake itself as the V3 writer, or get a
  third-party tool that writes spec-compliant V3 manifest avro.
  Snowflake's `full` V3 claim per icebergmatrix.org remains
  unverified by us, but not invalidated.
- **2026-05-26 (even later)** — Patched `testbed/_static_catalog.py`
  to emit a genuinely spec-compliant V3 manifest avro: subclassed
  `ManifestWriterV2` and `ManifestListWriterV2` to override
  `new_writer()` and `__enter__()` respectively, using `V3` instead of
  `DEFAULT_READ_VERSION` (=2) for the record schema. Verified the V3
  fields are now populated in the avro bytes (`first_row_id` values
  0, 1000, ... per data file).
- **2026-05-26 (final)** — Promoted `testbed/v3_geometry.py` to write
  parquet files with native `Geometry(crs=)` logical type (GeoParquet
  2.0), via `geoarrow-pyarrow`. Combined with the V3 manifest avro
  work and spec-minimal metadata.json, the testbed now ships a
  reference V3 catalog that *engines should be tested against*. We
  no longer treat any single engine's strictness as the bar to clear;
  if Snowflake/Databricks/DuckDB/BigQuery reject this fixture, those
  are engine-side gaps to file. Snowflake's unmanaged reader is the
  strictest — it still rejects because it requires the V3 row-lineage
  columns physically present even when `row-lineage: false` in
  metadata. We document that as a Snowflake-side issue rather than
  bend the reference catalog to match it.
- **2026-05-26 (much later)** — Ran the Snowflake-managed V3 path
  (Path 1 from the engines/snowflake/README): `CREATE ICEBERG TABLE
  ... GEOMETRY ... ICEBERG_VERSION=3`. **Worked end-to-end at L3+.**
  Discovered: (1) `ICEBERG_VERSION=3` is required (default is V2);
  (2) Snowflake's V3 manifest geometry bounds use `packed_xy_le`
  (LE-double-X then LE-double-Y, 16 bytes) — empirically matches our
  testbed's encoding; (3) Snowflake's V3 parquet files use the native
  `Geometry(crs=)` Parquet logical type (GeoParquet 2.0); (4) Their
  V3 parquet files physically contain `METADATA$RL_ROW_ID` and
  `METADATA$RL_LAST_UPDATED_SEQUENCE_NUMBER` row-lineage columns.
  Iterated our V3 writer to match Snowflake's metadata.json shape
  exactly (next-row-id / statistics / partition-statistics; bare
  `"geometry"` type token; iceberg.schema manifest-meta key). All
  structural diffs eliminated. Still rejected with "incomplete state"
  on the unmanaged read path — almost certainly because our parquet
  data files lack the physical row-lineage metadata columns
  Snowflake's V3 reader requires.
- **2026-05-28** — Repo split into geo / catalog tracks; catalog-access
  and REST-catalog interop detail consolidated in
  [STATUS_CATALOG.md](./STATUS_CATALOG.md). No V3-geometry cell changes.
- **2026-05-28 (later)** — DuckDB N3 unlocked via
  [duckdb-iceberg PR #1013](https://github.com/duckdb/duckdb-iceberg/pull/1013).
  Built the PR locally (base `v1.5-variegata`, DuckDB submodule pinned
  at `2a172f10f4`); verified the spatial predicate no longer crashes
  and returns the correct row count on both the hand fixture and
  Snowflake's managed V3 table. EXPLAIN ANALYZE shows full-scan
  behavior (10/10 and 7/7 files read) — N4 unchanged because the PR
  discards geometry bounds rather than decoding them (in-source
  comment confirms this is intentional and deferred).
- **2026-05-28 (later still, sharpened)** — Built `testbed/v3_minimal.py`
  (a V3 fixture with no geometry: `id` STRING + `n` INT, same V3
  metadata + V3 manifest avro shape as `v3_geometry`) to isolate "V3
  reader works" from "V3 geometry works". Confirmed empirically on
  BigQuery, Dataproc Serverless 2.3, and EMR Serverless 7.13: all
  three read v3_minimal end-to-end (`COUNT(*) = 10000`, schema parsed
  cleanly). So V3 has shipped on all three; geometry specifically is
  the missing piece. That promotes the original "L0 across the board"
  finding to a sharper "L0 on V3 geometry, but V3 itself reads fine"
  — geometry isn't on the near-term roadmap on these engines.
- **2026-05-28 (later still)** — Added managed-Spark coverage: probed
  **Dataproc Serverless 2.3** (Spark 3.5.3) and **EMR Serverless 7.13**
  (Spark 3.5.6-amzn-2) — both L0 on V3 geometry with the same root
  cause as Sedona+Iceberg-Spark 1.7.1: `iceberg-spark-runtime` can't
  map the V3 geometry type to a Spark type
  (`UnsupportedOperationException: Cannot convert unknown type to
  Spark: geometry`). That's three independent Spark variants now —
  the OSS reference and both of the major managed Spark services — all
  failing identically. The gap is in upstream iceberg-spark-runtime,
  not engine-specific patches. icebergmatrix.org lists "EMR (8.0
  Spark): Full" for V3 Geometry, but `emr-8.x` isn't yet a releasable
  label (latest is `emr-7.13.0`); empirically Spark is L0 everywhere
  we can actually run it.
- **2026-05-28 (even later)** — Snowflake **unmanaged** V3 read
  unlocked. *First, incorrect, claim*: that Snowflake required its own
  internal lineage columns (`METADATA$RL_ROW_ID` /
  `METADATA$RL_LAST_UPDATED_SEQUENCE_NUMBER` at field IDs 2147483540/39),
  uppercase column names, and a `last-column-id: 4` bump.
  *Correct claim after bisection*: the actual requirements are just (a)
  V3 spec compliance in the snapshot block — `first-row-id` and
  `added-rows` must be set (Snowflake reports `Invalid added-rows
  (required when first-row-id is set)` when only one is present, and
  `incomplete state` when both are missing); and, for the spatial
  predicate to evaluate without erroring on the `packed_xy_le` geom
  bound, (b) populated `value_counts` + `null_value_counts` and (c)
  populated `lower_bounds` / `upper_bounds` for the ID column too. With
  those three properties present, `testbed/v3_geometry.py` reads
  end-to-end on Snowflake unmanaged: CREATE → 10000 rows → spatial
  predicate 1000 rows → bytes_scanned ~25 KB vs ~256 KB on a full GEOM
  scan, confirming geometry-bound pruning at L3. (Removed the
  short-lived `v3_geometry_snowflake_lineage.py` companion fixture: it
  was based on the incorrect requirement and is no longer needed.)
  Reframes the Snowflake row from "managed-only" to "managed +
  unmanaged" at N1–N4.
- **2026-09-22** — Checked the fixtures against **GeoParquet 2.0.0-rc.1**
  ([released 2026-07-19](https://github.com/opengeospatial/geoparquet/releases/tag/v2.0.0-rc.1)). The RC makes the native Parquet
  `GEOMETRY`/`GEOGRAPHY` logical types its foundation (what we already
  wrote) but still requires the `geo` footer key for conformance; our
  files were "parquet-geo-only". The three V3 writers now emit the `geo`
  metadata (per-file `bbox`, `edges: "spherical"` on geography) and
  `gpio check all` passes on the rebuilt files. Iceberg-side results are
  unchanged (DuckDB runner 5/5, pyiceberg parses). Also noted: the RC
  dropped the 1.1 `covering` bbox column and a post-RC change
  ([#302](https://github.com/opengeospatial/geoparquet/pull/302), 2026-09-07) brought it back as optional for
  page-level pruning — see SPEC.md. pyarrow writes row-group geo
  statistics for `GEOMETRY` only, not `GEOGRAPHY`.
- **2026-10-09** — Re-verified on **DuckDB 1.5.6** (2026-09-28; iceberg
  extension `890b78a9c`, which includes [PR #1030](https://github.com/duckdb/duckdb-iceberg/pull/1030), merged 2026-06-10;
  spatial `04270fe`). DuckDB N3 ✅ on the stock release (no local
  build), N4 ✅ with `ST_Intersects_Extent` / `&&`, alone or combined
  with `ST_Intersects` (1/10 files on `v3_geometry` and
  `v3_geometry_lineage`, 2/7 on Snowflake-managed V3); plain
  `ST_Intersects` still full-scans (10/10, 7/7): 1.5.6 pushes it into
  the `ICEBERG_SCAN` filters, but the zonemap check only understands
  bbox predicates and no bbox pre-filter is derived from it — over HTTP
  it fetches all 10 data files (~187 KB) vs 1 file (~19 KB) for the
  extent predicate. **DuckDB V3 geometry moves L2 → L3.** Upstream
  closed [#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002) on 2026-09-01. Geography unchanged:
  `iceberg_scan` still fails with `Not implemented Error: Geography
  support`, while `read_parquet` types it `GEOMETRY` and returns the
  1000 California rows. **PyIceberg 0.12.0** (2026-09-01) adds
  `GeometryType` / `GeographyType` — parses our V3 fixtures and plans
  files, but `to_arrow()` rejects the `geoarrow.wkb` extension type
  (now tracked in [apache/iceberg-python#4079](https://github.com/apache/iceberg-python/issues/4079); a Snowflake-written V3 table fails the same way);
  `write_manifest()` still rejects `format_version=3`, so the V3
  writer subclasses in `testbed/_static_catalog.py` stay. The fixture
  writers now declare `GeometryType()` / `GeographyType()` directly
  (requires pyiceberg ≥ 0.12.0); fixtures rebuild byte-identical under
  pyiceberg 0.12.0 / pyarrow 25.0.1 / geoarrow-pyarrow 0.3.0. `gpio
  check spec` (geoparquet-io 1.6.0) passes 34/34 on all 30 V3 files
  (geography files carry the expected "missing geospatial statistics"
  warning). **Apache Iceberg 1.12.0** (2026-09-30) ships the Spark 4.1
  geometry work — the Spark rows are now re-testable. **BigQuery**
  re-tested 2026-09-21: still `Unknown Iceberg type "geometry"` /
  `"geography"` at `CREATE EXTERNAL TABLE` (release notes show no V3
  geo change since). Snowflake / Databricks / Oracle rows not re-run
  (no credentials on the verifying machine).
