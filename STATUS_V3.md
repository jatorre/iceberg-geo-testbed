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
| **DuckDB 1.5.3** | ✅ — schema parses, `COUNT(*)` works | ❌ — `Unimplemented type for cast (BLOB → GEOMETRY('OGC:CRS84'))` on the parquet reader path | ❌ — bound deserializer (`IcebergValue::DeserializeValue`) has no GEOMETRY branch; crashes on first spatial predicate. See [duckdb-iceberg#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002). | ❌ — blocked by N2/N3 today; tracking PR. Per PR description: *"This PR doesn't add support for upper bound and lower bounds for the geometry type. That is something we will add later."* | ❓ — not tested |
| **BigQuery / BigLake** (2026-05) | ❌ — `Unknown Iceberg type "geometry(OGC:CRS84)"` at `CREATE EXTERNAL TABLE` | ❌ — blocked by N1 | ❌ — blocked by N1 | ❌ — blocked by N1 | ❌ — `GEOGRAPHY` type also explicitly unsupported per [icebergmatrix.org](https://icebergmatrix.org/) |
| **Sedona 1.6.1 + Iceberg-Spark 1.7.1** | ❌ — `Cannot parse type string to primitive: geometry(OGC:CRS84)` on `spark.read.format('iceberg').load(...)` | ❌ — blocked by N1 | ❌ — blocked by N1 | ❌ — blocked by N1 | ❌ — `iceberg-spark-runtime` rejects Sedona's Geometry UDT: `java.lang.UnsupportedOperationException: User-defined types are not supported at SparkTypeVisitor.visit`. Even the reference V3 toolchain can't write native geometry today. |
| **Snowflake (preview)** | ❓ — **untested**. Our hand-written V3 fixture is not spec-compliant: `format-version: 3` in metadata.json but V2-format manifest avro (pyiceberg 0.11.1 hardcodes V2 manifest writes). Snowflake's V3 reader correctly catches this inconsistency and rejects with `Iceberg table 'V3_GEOMETRY' is V3 but is in an incomplete state.` Polaris and Iceberg-Spark are more permissive and accept the hybrid, which is what let us probe DuckDB's V3 path. To test Snowflake's claimed `full` V3 support per [icebergmatrix.org](https://icebergmatrix.org/), we'd need either (a) a Snowflake-managed V3 table that Snowflake itself writes (testable; pending), (b) a true V3 manifest avro writer in pyiceberg ([#1818](https://github.com/apache/iceberg-python/issues/1818)), or (c) hand-implement V3 manifest avro in this testbed. | ❓ | ❓ | ❓ | ❓ |
| **Databricks (DBSQL 2026.10)** | ❌ — `[UNSUPPORTED_DATATYPE] Unsupported data type "GEOMETRY"` at parser level (same for `GEOGRAPHY`). [Databricks's own docs](https://docs.databricks.com/aws/en/iceberg/) acknowledge geospatial as a V3 feature; icebergmatrix.org states *"Geospatial types are explicitly not supported in Databricks Iceberg v3 implementation."* | ❌ | ❌ | ❌ | ❌ |
| **Oracle ADB 26ai (23.26.2.2.0)** | ❓ — V3 metadata path-based reads are blocked separately by Oracle's parser strictness on pyiceberg-emitted manifests (see V2 status). Can't isolate the V3 question until the V2 read works. | ❓ | ❓ | ❓ | ❓ — not in icebergmatrix.org's coverage |
| **Apache Polaris** (reference REST catalog) | ✅ — registers V3 tables via `POST .../register` once metadata includes the required `next-row-id` and `row-lineage` fields (caught a real pyiceberg 0.11.1 gap we patched in `testbed/_static_catalog.py`) | n/a — Polaris is a catalog, not a query engine | n/a | n/a | n/a |
| **PyIceberg 0.11.1** | ⚠️ — V3 metadata read landed but `GeometryType` writer is missing; tracked at [iceberg-python#1818](https://github.com/apache/iceberg-python/issues/1818) | ⚠️ — returns the column as Arrow `binary`, no typed geometry | n/a (library) | n/a | ❌ |
| **OSS Spark 4.1 / Flink 2.2** (not in this testbed) | ❓ | ❓ | ❓ | ❓ | ❓ — per icebergmatrix.org: *"V3 geometry type support is not yet documented"* |

## What this picture tells you

As of mid-2026:

- **N1 is implemented on 1 of 6 engines we tested** (DuckDB). One more
  (Snowflake) claims it in preview; we couldn't verify.
- **N2–N4 are implemented on 0 engines** we tested (Snowflake's
  preview claims are unverified).
- **W1 is implemented on 0 engines** we tested. Sedona/Iceberg-Spark
  — the supposed reference implementation — has a *known and named*
  blocker: `iceberg-spark-runtime` doesn't have a UDT→IcebergGeometryType
  mapper, so the official write path doesn't exist either.

This is the empirical reason
[**STATUS_V2.md**](./STATUS_V2.md) and the
[**GeoIceberg V2 spec**](./SPEC.md) exist. The V3 story will mature.
Until it does, the convention bridges the gap.

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
  `DEFAULT_READ_VERSION` (=2) for the record schema. Without this
  fix, pyiceberg's writer would silently drop V3-only fields like
  `data_file.first_row_id` and `manifest_file.first_row_id` even
  when the file format-version says 3. Verified the V3 fields are
  now populated in the avro bytes (`first_row_id` values 0, 1000,
  2000, ... per data file). **Snowflake still rejects with the same
  "incomplete state" error.** So there's another V3-spec requirement
  beyond manifest avro structure that we're missing — possibly schema
  field-level markers (`initial-default` / `write-default`), partition
  spec V3 changes (`source-ids` plural), or Snowflake-specific
  requirements not in the public V3 spec. Worth opening a Snowflake
  support follow-up to find out which.
