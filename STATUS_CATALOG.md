# Iceberg catalog interoperability — engine support status

**Last verified: 2026-05-28.** Living document; PRs welcome.

This is the **catalog track** of the testbed. It asks a question separate from
geospatial: *can you publish a public Apache Iceberg dataset that any engine can
consume?* The geospatial story is in [STATUS_V2.md](./STATUS_V2.md) /
[STATUS_V3.md](./STATUS_V3.md); this file is purely about **how engines reach an
Iceberg table** — the catalog/auth/storage plumbing — independent of what's in it.

The short answer: **the open query-engine ecosystem (DuckDB, Trino, Spark,
PyIceberg) consumes an open Iceberg catalog directly; the managed warehouses
mostly don't** — they gate it behind mandatory auth, partner-certified
connectors, or per-engine governance. The format is open; the *catalog door* is
not. See [BLOG_CATALOG.md](./BLOG_CATALOG.md) for the narrative.

## Two access modes

An Iceberg table can be reached two different ways, and engines support
different subsets:

- **(A) Catalog consumption** — the engine talks to an Iceberg REST Catalog
  (IRC) endpoint (`/v1/config`, `/v1/{prefix}/namespaces`, `…/tables/{t}`) and
  the catalog hands back table metadata. Discovery + governance + multi-table.
- **(B) Per-table `metadata.json` pointer** — the engine is pointed at a single
  table's `metadata.json` URL directly, no catalog. Lowest-friction; no
  discovery, but works on the widest set of engines.

A Portolan catalog exposes **both**: the IRC surface for clients that support it,
and — because the catalog's `loadTable` response literally returns each table's
`metadata.json` URL — the per-table pointer as a universal fallback.

## The three governing axes: storage × catalog × auth

Whether a read works *at all* is a product of three orthogonal axes, not just
"engine × format":

1. **Storage backend** — S3 is the lingua franca; GCS is second-class in
   several engines (Databricks's direct-read path rejects the `gcs://` scheme;
   Snowflake-on-GCS vs Snowflake-on-AWS behave differently). Oracle is the
   exception that proves it's not *always* storage — it fails on both.
2. **Catalog mechanism** — static `metadata.json` on a bucket / generic Iceberg
   REST / named partner catalog (Glue, HMS, Snowflake Horizon, Unity). Engines
   support wildly different subsets.
3. **Auth mode** — public/anonymous, credential-vended, keyed-vs-keyless,
   OAuth/JWT, long-lived-vs-temporary. Most warehouse connectors **mandate** an
   auth object even for public data.

### The empty-credentials trap

Many connectors have *no first-class anonymous path* — a credential object is
mandatory in the API even for public data. Worse, raw object stores **reject an
invalid/dummy credential** rather than ignoring it: send `Authorization: Bearer
<dummy>` to a public object and S3 returns `400 Unsupported Authorization Type`,
GCS returns `401 AuthenticationRequired`. So you can neither omit the credential
(connector refuses) nor fake it (store refuses). This is the core reason a
warehouse can't read a public static catalog directly.

## Engine consumption table

| Engine | (A) Consume IRC catalog | (B) Per-table `metadata.json` | Notes |
|---|---|---|---|
| **DuckDB 1.5.3** | ✅ `ATTACH … (TYPE iceberg, AUTHORIZATION_TYPE 'none')` — anonymous IRC | ✅ `iceberg_scan('…/metadata.json')` | The reference open client. Consumes our static catalog with zero credentials. Also consumed Snowflake Horizon (live IRC) with a JWT. |
| **Trino / Spark / PyIceberg** | ✅ (by spec — unauthenticated or token IRC) | ✅ | PyIceberg verified against Google BigLake's public catalog (token + header). Standards-conformant IRC clients. |
| **Snowflake (GA May 2026)** | ❌ **direct** — `ICEBERG_REST` mandates `REST_AUTHENTICATION`; dummy token rejected by the object store. ✅ **via a permissive CDN/edge front** (CloudFront or a Cloudflare Worker that drops `Authorization`) + an external volume. Verified end-to-end (`COUNT=10000`, bbox `=196`). | ✅ managed write path; ❌ unmanaged external read | The one warehouse we got reading a (CDN-fronted) static Portolan catalog end-to-end. |
| **Oracle ADB 26ai** | ❌ — `MOUNT_ICEBERG` requires a real `oauth2`/`gcp_oauth2`/`aws_role_arn`/`secret_id` credential (no static bearer) + a partner catalog type. Even via a Worker that serves a fake token endpoint, `MOUNT` only stores config — it defers/never connects (confirmed: zero requests reached the Worker). | ❌ — `ORA-20000: Failed to generate column list` (the reader bug, storage-independent) | Doubly blocked: catalog auth (needs token handshake) *and* the reader bug. |
| **Databricks (DBSQL 2026.10)** | ❌ **no generic IRC connector, by design** — `CREATE CONNECTION TYPE iceberg`/`iceberg_rest` → `CONNECTION_TYPE_NOT_SUPPORTED`. Only named partners (`GLUE`/`HIVE_METASTORE`/`SNOWFLAKE`/`DATABRICKS`). Catalog federation is partner-gated; direct-from-storage read additionally blocked for Snowflake-on-GCP by a `gcs://`-vs-`gs://` scheme mismatch. | ❌ no credential-less pointer — `USING ICEBERG LOCATION` requires a UC **external location** (`NO_PARENT_EXTERNAL_LOCATION_FOR_PATH`); no `iceberg.\`path\`` reader; `read_files` has no iceberg format | Reached our V2 data only via **`TYPE snowflake` query federation** (JDBC pushdown). Everything external routes through Unity Catalog governance — no anonymous door. |
| **AWS Glue catalog federation** | ❌ — no generic-IRC connection type in the API; federation targets are partner-specific (Snowflake / Databricks / Redshift). Reads data from S3 only. | n/a | Glue *serves* IRC (its own endpoint); as a *consumer* it's partner-gated like Databricks. |
| **BigQuery / BigLake** | ❌ — no remote-IRC *consumer* (BigLake metastore is Google's own catalog that BigQuery consumes; it doesn't attach arbitrary external IRC catalogs) | ✅ **credential-less** — `CREATE EXTERNAL TABLE … OPTIONS(format='ICEBERG', uris=['…/metadata.json'])`; V2 → L3 pruning, V3 geometry → L0 (type rejected) | The cleanest per-table path: point at the URL, no credential ceremony. |
| **Microsoft Fabric** | ⚪ not tested (no access) | ⚪ not tested | |

## The static "Portolan" catalog (reference implementation)

[`testbed/static_rest_catalog.py`](testbed/static_rest_catalog.py) pre-renders
the IRC read endpoints as plain JSON objects on a bucket — a **fully static,
serverless Iceberg REST catalog**. No server, no DB; CDN-friendly. It wraps the
existing fixtures (namespaces `v2`, `v3`) and is published public on:

- **GCS:** `https://storage.googleapis.com/cartobq-iceberg-geo-testbed/catalog`
- **S3:** `https://carto-iceberg-geo-testbed-public.s3.us-east-1.amazonaws.com/catalog`
  (and `s3native` — `s3://` data paths, for warehouse external volumes)

```sql
-- DuckDB consumes it directly, no credentials:
ATTACH 'geo' AS cat (TYPE iceberg,
  ENDPOINT 'https://storage.googleapis.com/cartobq-iceberg-geo-testbed/catalog',
  AUTHORIZATION_TYPE 'none');
SELECT COUNT(*) FROM cat.v2.v2_flat_columns;   -- 10000
```

Build/publish: `python -m testbed.static_rest_catalog --target={gcs|s3|s3native} --publish`.

### Bridging the warehouses: a permissive CDN / edge front

The warehouse blocker is a **protocol mismatch**: Snowflake speaks
`BEARER`/`OAUTH`/`SigV4-for-AWS-services`; raw S3/GCS speak SigV4-for-s3 /
GCS-OAuth / anonymous. They don't intersect, and a public credential can't
bridge it (the engine won't sign object-store requests). The fix is a front
that **ignores the `Authorization` header**:

- **CloudFront** over the S3 bucket with the managed *CachingOptimized* policy
  (doesn't forward `Authorization`) — config-only, no server. Snowflake reads
  through it end-to-end (catalog auth absorbed; data via external volume with
  `ALLOW_WRITES=FALSE` + a scoped read-only IAM role).
- **A shared Cloudflare Worker** ([`portolan-proxy/`](portolan-proxy/)) —
  stateless, path-encoded origins (`/gcs/<bucket>/<prefix>` or
  `/s3/<region>/<bucket>/<prefix>`), drops `Authorization`, **and serves a fake
  `/v1/oauth/tokens`** (the thing a dumb CDN can't — for OAuth clients).
  Deployed at `portolan-irc-proxy.carto-portolan.workers.dev`. Snowflake
  consumes a catalog through it end-to-end. It only proxies the tiny catalog
  JSON; engines read the data directly from storage, so bandwidth is trivial.

This means "every creator provisions their own CloudFront" can become "use one
shared proxy URL" — though it reintroduces a (tiny, stateless) shared service,
and the data layer still needs the engine's normal storage access.

## Contrast: Google BigLake public Iceberg catalog

Google publishes public Iceberg datasets via a real IRC endpoint
(`https://biglake.googleapis.com/iceberg/v1/restcatalog`, warehouse
`gs://biglake-public-nyc-taxi-iceberg`). It's the closest production analog —
and a useful foil:

| | **Portolan (this repo)** | **Google BigLake public** |
|---|---|---|
| Architecture | static files, serverless | managed server |
| Consume auth | **none** (anonymous) for open clients; CDN front for warehouses | Google OAuth2 token **+** mandatory `X-Goog-User-Project` header |
| DuckDB | ✅ (`AUTHORIZATION_TYPE 'none'`) | ❌ `403` — can't send the quota header |
| PyIceberg / Spark | ✅ | ✅ (with token + header) — verified, read `nyc_taxicab` rows |
| Domain | geospatial (V2/V3) | general (NYC taxi); no geo |
| Cost / governance | ~free, no governance/quota | managed, quota-tracked, governed |

Even Google's "public" catalog is gated behind a Google project; Portolan trades
a server's governance for radical openness — it's the only one a credential-less
DuckDB can read.

## Cells we'd love filled

- **Microsoft Fabric** — does it consume a generic remote IRC catalog?
- **Trino / Spark hands-on** against the static catalog (expected ✅; only
  spec-reasoned + PyIceberg-verified so far).
- **Whether a UC external location lets `USING ICEBERG LOCATION` actually read**
  our table on Databricks (the storage-credential ceremony, untested).
