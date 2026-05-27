# Databricks engine runner

Status as of **2026-05-23** (original) and **2026-05-26** (Snowflake
federation update below). Databricks SQL (DBSQL `2026.10`).

## Update 2026-05-26 — Databricks ↔ Snowflake federation (V2 GeoIceberg)

We retested the "can Databricks reach our catalogs" question against a
Snowflake-managed **V2 GeoIceberg** table (bbox doubles + `geom_wkb
BINARY`, no `GEOMETRY` type token — see
`engines/snowflake/_managed_v2_test.py`). Two corrections + one new
finding:

**1. `CREATE CONNECTION TYPE snowflake` works — our earlier claim was
too strong.** The original test below only tried `TYPE iceberg` and
`TYPE ICEBERG_REST` (both `CONNECTION_TYPE_NOT_SUPPORTED`). It never
tried `TYPE snowflake`, which *is* a valid connector. On the shared
CARTO metastore it returns `PERMISSION_DENIED` (no `CREATE CONNECTION`
grant), not a type error — proving the type is recognized. On a personal
**Databricks Free Edition** sandbox (where you're metastore admin), it
creates cleanly.

**2. Query federation: ✅ V2 + WKB is portable into Databricks today.**
Via `CREATE CONNECTION TYPE snowflake` + `CREATE FOREIGN CATALOG`,
Databricks reads the Snowflake-managed V2 table: schema correct
(`geom_wkb` surfaces as `binary`), `COUNT(*)=10000`, bbox predicate
`=196`, polygon point-in-poly `=1000` — all matching Snowflake exactly.
`st_geomfromwkb(geom_wkb)` parses the WKB into proper POINTs and
`st_intersects(...)` runs correctly. The WKB parsing happens in
Databricks compute (it's a Databricks function over bytes pulled from
Snowflake). This is the V2 convention working as a portable bridge.

**3. Catalog federation (direct-from-GCS Iceberg read): ⚠️ falls back
to JDBC on Snowflake-on-GCP.** We set up the full path on Free Edition —
a dedicated read-only GCP SA, a Databricks **storage credential**
(via the UC REST API; SA-key path, since the keyless Workload Identity
Federation path is disabled on Free Edition), and a read-only
**external location** over the bucket (`validate-storage-credentials`
returns READ/LIST = PASS). Despite that, every `EXPLAIN` shows
`SnowflakePlan` / `SnowflakeRelation.scala` — reads go via **JDBC
pushdown**, never directly from GCS.

Root cause (corroborated): Databricks's direct-read qualification only
accepts URI schemes `s3/s3a/s3n/abfs/abfss/gs/r2/wasb/wasbs`, but
**Snowflake-on-GCP vends its Iceberg metadata location with the
`gcs://` scheme** (`SYSTEM$GET_ICEBERG_TABLE_INFORMATION` →
`gcs://cartobq-…`). Databricks can't match a `gcs://` metadata location
to any governed external location — it explicitly rejects `gcs://`
(`url has invalid URI scheme gcs. Valid URI schemes include … gs …`) —
so it silently falls back to JDBC. This is **GCP-specific**: on AWS
(`s3://`) or Azure (`abfss://`) the schemes would line up and direct
reads would likely qualify.

Net: Databricks *can* consume a Snowflake-managed Iceberg catalog (query
federation), but the Iceberg-native direct read doesn't engage for
Snowflake-on-GCP because of the `gcs://`-vs-`gs://` scheme mismatch.

Repro: `engines/databricks/_federation_v2.py` (sandbox creds at
`~/.config/iceberg-geo-testbed/databricks-sandbox.txt`).

## Headline finding (original 2026-05-23, partially superseded by above)

Databricks fully supports Iceberg V2 reads — **but only through specific
named catalog providers**. There is no generic Iceberg REST catalog
client in DBSQL, and no static-`metadata.json`-on-cloud-storage path,
both confirmed by independent sources:

- Databricks's [own announcement blog](https://www.databricks.com/blog/announcing-full-apache-iceberg-support-databricks):
  Catalog Federation supports "external catalogs such as **AWS Glue,
  Hive Metastores, and Snowflake Horizon Catalog**" — that's the
  exhaustive list.
- [icebergmatrix.org](https://icebergmatrix.org/) `databricks:polaris:v2`:
  `unknown — Databricks documentation does not mention Polaris catalog
  integration`. The `databricks:rest-catalog:v2: full` cell is about
  *Unity Catalog SERVING* the REST API, not consuming external ones.

Our hands-on confirmed the *generic* Iceberg paths don't exist:
`CREATE CONNECTION TYPE iceberg` and `CREATE CONNECTION TYPE
ICEBERG_REST` both error with `CONNECTION_TYPE_NOT_SUPPORTED`. **But
`TYPE snowflake` does work** (see the 2026-05-26 update above) — so the
"named catalog providers only" framing is right, but Snowflake *is* one
of those providers and is reachable. There's still no generic Iceberg
REST client and no static-`metadata.json` path.

**This is by design, not a temporary gap.** Databricks's stated position
is that it deliberately does *not* offer a generic Iceberg-REST (IRC)
connector: because IRC implementations vary in practice, it builds and
certifies per-partner connectors (the Lakehouse Federation model, also
how Glue / Fabric / BigQuery operate) rather than accepting any
spec-compliant endpoint, and prioritizes new endpoints by customer
demand. So a self-hosted Polaris / Nessie / Lakekeeper / generic-IRC
catalog is **not expected to become directly federatable** — the
supported path is to land your tables in one of the certified named
catalogs (Glue / HMS / Snowflake Horizon / Unity). The reliability
argument is real (this testbed shows IRC implementations genuinely
diverge — `gcs://` vs `gs://`, V3 row-lineage, etc.), but it does leave
the "open standard" practically gated at the connector layer:
conformance to the spec isn't sufficient for access; a commercial
certification is.

This is a **managed-warehouse-federation vs query-engine** split, not
"Databricks is uniquely closed." AWS Glue's catalog federation looks
similarly partner-gated (its API exposes no generic-IRC connection type;
documented targets are Snowflake / Databricks / Redshift), so Databricks's
"same approach as Glue" is defensible. The *generic* IRC-consumer role is
filled by the query engines and clients: DuckDB, Trino, Spark, pyiceberg —
and Snowflake's `CATALOG_SOURCE = ICEBERG_REST` integration. We
demonstrate the open path works by serving our own **fully static,
serverless IRC catalog** (see the repo README) and reading it with DuckDB
`ATTACH` — zero certification, an arbitrary endpoint. The standard isn't
the blocker; the warehouses' federation product choices are.

> *"Iceberg tables in Unity Catalog do not support a LOCATION clause."*
> — [Databricks docs on Iceberg](https://docs.databricks.com/aws/en/iceberg/)

This isn't a quirk of our test — it's an explicit Databricks product
gap. To get our metadata into Databricks would require *first*
registering the tables in Glue, HMS, or Snowflake Horizon, then
federating. That's significant cross-cloud setup, tangential to the
V3 geometry question this testbed is asking.

## What we did test: Databricks-managed V3

To still get a meaningful Databricks row in the matrix, we tried having
Databricks **write its own** V3 Iceberg table with a `GEOMETRY` column.

### Geo types: supported in Delta, NOT in Iceberg (verified 2026-05-26)

The earlier "Databricks doesn't recognize the geo type tokens" claim was
too coarse. Re-tested precisely on DBSQL `2026.10`:

| What | Result |
|---|---|
| `typeof(st_point(0,0))` | `geometry(0)` — the GEOMETRY type **exists** (functions return typed `geometry(SRID)`, not strings) |
| bare `GEOMETRY` / `GEOGRAPHY` in DDL or `CAST` | ❌ `UNSUPPORTED_DATATYPE` — the type name **requires** the `(SRID)` parameter |
| `GEOMETRY(4326)` / `GEOGRAPHY(4326)` column in **Delta** | ✅ **works** |
| `GEOMETRY(4326)` column in **Iceberg** | ❌ `DELTA_ICEBERG_WRITER_COMPAT_VIOLATION.UNSUPPORTED_DATA_TYPE` (`IcebergWriterCompatV1`) |
| same with `format-version=3` | ❌ `IcebergWriterCompatV3 does not support the data type geometry(4326)` |

So the accurate statement: **Databricks supports `GEOMETRY(SRID)` /
`GEOGRAPHY(SRID)` typed columns — but only in Delta. The Iceberg writer
(`IcebergWriterCompatV1`/`V3`) explicitly rejects them**, so geo types
don't pass through the Delta→Iceberg (UniForm) compatibility layer.

The `DELTA_ICEBERG_WRITER_COMPAT_VIOLATION` error name is itself a tell:
**Databricks "Iceberg" tables are Delta tables with an Iceberg-compat
writer on top**, not a native Iceberg engine — which is exactly why the
geo type stops at the Iceberg boundary.

**Roadmap:** geo support in the Iceberg path is *not* there yet as of
2026-05-26 but is **likely coming soon** — worth re-testing periodically.
Generic Iceberg-REST-catalog federation is **not supported by design**
(certified per-partner connectors only — see the headline finding); it
is not expected to change generically.

## Matrix row

| Fixture | Level | Detail |
|---|---|---|
| `v2_flat_columns` | n/a | Can't read our metadata; structural blocker. Could be created from scratch in a Databricks-managed table, but that's no longer an interop test. |
| `v2_bbox_struct`  | n/a | same |
| `v3_geometry`     | **L0** | `GEOMETRY(SRID)`/`GEOGRAPHY(SRID)` work in **Delta** but the Iceberg-compat writer rejects them (`DELTA_ICEBERG_WRITER_COMPAT_VIOLATION`, V1 and V3). Geo-in-Iceberg likely coming soon (not available 2026-05-26). |

## What would unblock it

To get a non-blocked Databricks row testing OUR metadata, we'd need to:

1. Stand up an Iceberg REST catalog (Tabular/Polaris/etc.) serving our
   metadata.
2. Register it as a foreign catalog in Databricks via Lakehouse Federation.
3. Then `CREATE FOREIGN TABLE` against it.

That's a real chunk of work for a single matrix row, and the V3
geometry result would still be L0 because the type isn't recognized in
the parser regardless. Worth revisiting once Databricks announces V3
geospatial GA.

## Files

- `_creds.py` — REST credentials loader (gcloud secret or env override).
- `_discover.py` — read-only state probe (catalogs, schemas, storage
  credentials, external locations).
- `_first_probe.py` — surfaced the static-metadata-read structural blocker.
- `_managed_v3.py` — surfaced the `UNSUPPORTED_DATATYPE: GEOMETRY` finding.
