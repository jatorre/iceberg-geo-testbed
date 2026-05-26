# Iceberg V3 native geometry — engine support status

**Last verified: 2026-05-26.** Living document; PRs welcome.

This table tracks each engine's implementation status against the
native V3 geometry/geography types defined in the
[Apache Iceberg spec](https://iceberg.apache.org/spec/#geometry).

Companion file: **[STATUS_V2.md](./STATUS_V2.md)** tracks the
*GeoIceberg V2 convention* — the workaround that delivers file-level
spatial pruning on engines that don't yet support V3 native geometry.
That convention is the recommended migration path while this table
remains mostly red.

## The reference catalog

The testbed ships **two V3 fixtures** demonstrating both
spec-permitted variants:

- **`v3_geometry`** — spec-minimal (`row-lineage: false`). The canonical
  reference; readers should accept this if they support V3 at all.
- **`v3_geometry_lineage`** — `row-lineage: true` + `_row_id` /
  `_last_updated_sequence_number` columns populated in each data file
  at the Iceberg V3 spec field IDs (`2147483545` / `2147483544`). For
  testing stricter readers that require lineage columns be present
  regardless of the metadata flag.

The shared properties of both fixtures:

- `format-version: 3` with `row-lineage: false` (spec-permitted off)
- Schema: `id: string`, `geom: geometry`. No CRS in the type token —
  CRS info lives in the parquet column's logical type.
- `next-row-id`, `last-column-id`, `statistics: []`,
  `partition-statistics: []` populated as the V3 spec expects.
- Manifest avro is real V3 (subclassed pyiceberg's V2 writers since
  the upstream library's V3 writer is incomplete; see
  `testbed/_static_catalog.py`). Includes `first_row_id` on data
  files, `first_row_id` on the manifest-list entry, and the
  `iceberg.schema` metadata key Snowflake-managed V3 emits.
- **Parquet data files use the native `Geometry(crs=)` logical type**
  via `geoarrow-pyarrow` (GeoParquet 2.0 style), with WKB-encoded
  point payloads. Same column-level encoding Snowflake's own managed
  V3 writer produces.
- Per-file geometry bounds in the `packed_xy_le` encoding (16 bytes:
  little-endian X, little-endian Y) — confirmed against Snowflake's
  own bound bytes byte-for-byte.

Published at `gs://cartobq-iceberg-geo-testbed/v3_geometry/` (public).
This is what V3 readers should be tested against.

A reader that rejects either fixture has an *engine-side* gap to file
against the engine vendor, not against this testbed.

### Cross-engine V3 interop verified

DuckDB reads **Snowflake's own managed V3 table** at exactly the same
level (L2) it reads our hand-written V3 fixture — same COUNT/SELECT/
ST_AsText results, same `ST_Intersects` bound-deserializer crash.
That's strong evidence that:

- Our catalog's V3 metadata + manifest avro + parquet structure is
  equivalent to Snowflake's for the parts DuckDB exercises.
- DuckDB's bound-deserializer gap is engine-side (same line of code
  fails whether the V3 table was written by us or by Snowflake) —
  filing it against `duckdb-iceberg` is the right move.

The Snowflake-managed V3 table lives at
`gs://cartobq-iceberg-geo-testbed-eu/managed-v3-geo.MLyhYkeQ/` and is
publicly readable; we use it as a second cross-engine reference.

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
| **DuckDB 1.5.3** | ✅ — schema parses, `COUNT(*)` works | ✅ — typed `geometry('ogc:crs84')` materializes; `ST_AsText(geom)` returns WKT cleanly. **Cross-verified at L2 against both our hand-written V3 fixture AND Snowflake's own managed V3 table** — proves cross-engine V3 interop works at this level. The earlier "BLOB→GEOMETRY cast missing" finding was caused by our parquet writing geom as plain BINARY; once we promoted to GeoParquet 2.0 native `Geometry(crs=)` typing, DuckDB reads it directly without the cast. | ❌ — bound deserializer (`IcebergValue::DeserializeValue`) has no GEOMETRY branch; crashes on first spatial predicate. Same failure pattern on Snowflake-written V3 (different bytes, same crash), so this is unambiguously a DuckDB-side gap. See [duckdb-iceberg#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002). | ❌ — blocked by N3 today; tracking PR. Per PR description: *"This PR doesn't add support for upper bound and lower bounds for the geometry type. That is something we will add later."* | ❓ — not tested |
| **BigQuery / BigLake** (2026-05) | ❌ — `Unknown Iceberg type "geometry(OGC:CRS84)"` at `CREATE EXTERNAL TABLE` | ❌ — blocked by N1 | ❌ — blocked by N1 | ❌ — blocked by N1 | ❌ — `GEOGRAPHY` type also explicitly unsupported per [icebergmatrix.org](https://icebergmatrix.org/) |
| **Sedona 1.6.1 + Iceberg-Spark 1.7.1** | ❌ — `Cannot parse type string to primitive: geometry(OGC:CRS84)` on `spark.read.format('iceberg').load(...)` | ❌ — blocked by N1 | ❌ — blocked by N1 | ❌ — blocked by N1 | ❌ — `iceberg-spark-runtime` rejects Sedona's Geometry UDT: `java.lang.UnsupportedOperationException: User-defined types are not supported at SparkTypeVisitor.visit`. Even the reference V3 toolchain can't write native geometry today. |
| **Snowflake (GA May 2026)** | ✅ verified via Snowflake-managed V3 path (`ICEBERG_VERSION=3` required — default for new Iceberg tables is V2; the error message `Unsupported data type 'GEOMETRY' for iceberg tables` doesn't hint at the V3 opt-in needed). Reads its own metadata cleanly. | ✅ — `SELECT geom` materializes as `GEOMETRY(4326)` | ✅ — `WHERE ST_INTERSECTS(geom, envelope)` returns the right rows | ✅ — `bytes_scanned=0` on the spatial predicate; Snowflake's manifest geometry-bound pruning is wired through end-to-end | ✅ — full write path works with `CATALOG='SNOWFLAKE'`. Writes Parquet-native `Geometry` columns (GeoParquet 2.0) + V3 manifest avro with `first_row_id` / geometry bounds populated using `packed_xy_le` (16-byte LE-double-X, LE-double-Y) — empirically matches our testbed's encoding. **Important caveat: V3 *unmanaged* read is not yet working.** We tried three external V3 fixtures (spec-minimal, spec-lineage at spec field IDs, Snowflake-lineage at Snowflake-internal field IDs) — all rejected with `incomplete state`. Since our metadata + manifests + parquets now byte-match Snowflake's own output, this isn't a strictness issue. The V3 unmanaged read path appears generally non-functional today. So Snowflake's V3 geometry support is real but currently bounded to "managed-only". Worth a Snowflake support follow-up. |
| **Databricks (DBSQL 2026.10)** | ❌ — `[UNSUPPORTED_DATATYPE] Unsupported data type "GEOMETRY"` at parser level (same for `GEOGRAPHY`). [Databricks's own docs](https://docs.databricks.com/aws/en/iceberg/) acknowledge geospatial as a V3 feature; icebergmatrix.org states *"Geospatial types are explicitly not supported in Databricks Iceberg v3 implementation."* | ❌ | ❌ | ❌ | ❌ |
| **Oracle ADB 26ai (23.26.2.2.0)** | ❓ — V3 metadata path-based reads are blocked separately by Oracle's parser strictness on pyiceberg-emitted manifests (see V2 status). Can't isolate the V3 question until the V2 read works. | ❓ | ❓ | ❓ | ❓ — not in icebergmatrix.org's coverage |
| **Apache Polaris** (reference REST catalog) | ✅ — registers V3 tables via `POST .../register` once metadata includes the required `next-row-id` and `row-lineage` fields (caught a real pyiceberg 0.11.1 gap we patched in `testbed/_static_catalog.py`) | n/a — Polaris is a catalog, not a query engine | n/a | n/a | n/a |
| **PyIceberg 0.11.1** | ⚠️ — V3 metadata read landed but `GeometryType` writer is missing; tracked at [iceberg-python#1818](https://github.com/apache/iceberg-python/issues/1818) | ⚠️ — returns the column as Arrow `binary`, no typed geometry | n/a (library) | n/a | ❌ |
| **OSS Spark 4.1 / Flink 2.2** (not in this testbed) | ❓ | ❓ | ❓ | ❓ | ❓ — per icebergmatrix.org: *"V3 geometry type support is not yet documented"* |

## What this picture tells you

As of mid-2026:

- **Snowflake is the first engine** we've verified that delivers N1–N4
  + W1 end-to-end (via the managed write path). With `ICEBERG_VERSION = 3`
  opted in, it accepts `GEOMETRY` columns, materializes them via SQL,
  applies spatial predicates correctly, and uses manifest geometry
  bounds for file pruning. Their *unmanaged* reader is stricter — see
  the table note.
- **DuckDB jumped from N1 to N2** when we upgraded our V3 parquet
  files to use the native `Geometry(crs=)` logical type
  (GeoParquet 2.0). The earlier BLOB→GEOMETRY cast gap turned out
  to be a fix-the-catalog issue, not a fix-the-engine issue. The
  remaining N3 gap (bound deserializer in `duckdb-iceberg`) is the
  only thing standing between DuckDB and full V3 file pruning today.
- **Other engines** (BigQuery, Sedona/Iceberg-Spark, Databricks)
  reject the V3 geometry type at parse, before reaching N2.
- **W1 outside of Snowflake**: Sedona/Iceberg-Spark — the supposed
  reference implementation — has `iceberg-spark-runtime` lacking the
  UDT→IcebergGeometryType mapper. pyiceberg V3 writes are incomplete
  (tracked at #1818). Snowflake-managed is the only working V3
  geometry writer we found.

This is the empirical reason [**STATUS_V2.md**](./STATUS_V2.md) and
the [**GeoIceberg V2 spec**](./SPEC.md) exist. The V3 story is *just*
beginning to ship in one engine (Snowflake managed). Until it spreads,
the V2 convention bridges the gap.

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

For DuckDB specifically: the missing `BLOB → GEOMETRY('OGC:CRS84')`
cast in the parquet reader path. Likely a small PR against
`duckdb-spatial` or `duckdb-iceberg`. The
[issue we filed](https://github.com/duckdb/duckdb-iceberg/issues/1002)
covers both N2 and N3.

### **N3 spatial predicate correctness**

DuckDB: `IcebergValue::DeserializeValue` in
`src/core/expression/iceberg_value.cpp` needs a `GEOMETRY` branch. We
have the encoding worked out in [docs/encoding.md](docs/encoding.md)
and a reproducible fixture in `testbed/v3_geometry.py`. The author of
PR #902 has acknowledged this as future work; a PR is welcome.

### **N4 manifest geometry-bound pruning**

The headline V3 feature. Even once N3 lands, this is a separate piece
of work — the engine has to *use* the manifest bounds for file
selection, not just deserialize them. The
[duckdb-iceberg#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002)
issue references this as the natural follow-up to the bound-deser fix.

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
