# Can you publish a *truly public* Apache Iceberg dataset? What we learned trying to make every engine read one

*2026-05-28*

Apache Iceberg is "open." The spec is open, the file format is open, and there's
an open REST Catalog (IRC) protocol that engines from DuckDB to Trino to
Snowflake all claim to speak. So publishing a public Iceberg dataset that anyone
can query should be as easy as publishing a public CSV or a GeoParquet file on a
bucket, right?

We tried it. The answer is more interesting — and more cautionary — than we
expected: **the format is open, but the catalog door is gated.** The open
query-engine ecosystem can read a public Iceberg catalog with zero credentials;
the managed warehouses mostly can't, because their connectors *mandate*
authentication that a public bucket can't satisfy. Here's the whole story,
backed by real runs.

## The naive attempt: a serverless static catalog

You don't actually need a running catalog server to speak IRC. The IRC read API
is just a handful of GET endpoints returning JSON:

```
GET /v1/config
GET /v1/{prefix}/namespaces
GET /v1/{prefix}/namespaces/{ns}/tables
GET /v1/{prefix}/namespaces/{ns}/tables/{table}   → the table's metadata
```

So we pre-rendered those responses as **static JSON objects on a bucket** — a
fully static, serverless Iceberg REST catalog (we call the pattern "Portolan").
No server, no database, CDN-friendly, a few dollars a month. It's published
public on GCS and S3. The generator is
[`testbed/static_rest_catalog.py`](testbed/static_rest_catalog.py).

And it works — beautifully — for the open clients:

```sql
-- DuckDB, zero credentials:
ATTACH 'geo' AS cat (TYPE iceberg,
  ENDPOINT 'https://storage.googleapis.com/cartobq-iceberg-geo-testbed/catalog',
  AUTHORIZATION_TYPE 'none');
SELECT COUNT(*) FROM cat.v2.v2_flat_columns;   -- 10000
```

DuckDB discovers every table across namespaces and reads them — anonymously,
against an arbitrary, uncertified, serverless endpoint. Trino, Spark, and
PyIceberg consume the same way (PyIceberg we verified directly). **Generic IRC
consumption is real and it works.**

Then we pointed the warehouses at it.

## The wall: warehouses mandate auth a public bucket can't satisfy

**Snowflake.** Its `CATALOG INTEGRATION ... CATALOG_SOURCE = ICEBERG_REST`
refuses to be created without `REST_AUTHENTICATION` — there is no "public"
option (`Missing option(s): REST_AUTHENTICATION`). Fine, we thought — give it a
throwaway bearer token; the catalog is public, the token is meaningless, the
bucket will ignore it. **It won't.** Send `Authorization: Bearer <dummy>` to a
public object and the object store *validates the header and rejects it*: S3
returns `400 Unsupported Authorization Type`; GCS returns `401
AuthenticationRequired`. So you can neither omit the credential (the connector
refuses) nor fake it (the store refuses). That's the **empty-credentials trap**,
and it's the crux of the whole problem.

**Oracle ADB.** `DBMS_CATALOG.MOUNT_ICEBERG` requires a real credential
(`oauth2` / `aws_role_arn` / `secret_id`) — `CREATE_CREDENTIAL` flatly rejects a
plain token. There's no static-bearer option at all; it wants an OAuth token
*handshake*.

**Databricks.** No generic IRC consumer exists — by design. `CREATE CONNECTION
TYPE iceberg` errors `CONNECTION_TYPE_NOT_SUPPORTED`; only named partners
(`GLUE`, `HIVE_METASTORE`, `SNOWFLAKE`, `DATABRICKS`) can back a foreign Iceberg
table. Databricks's position, confirmed directly: they deliberately *don't*
accept arbitrary spec-compliant endpoints; they certify per-partner connectors
and prioritize by customer demand. (Their reliability rationale is real — IRC
implementations genuinely diverge — but it leaves the open standard gated at the
connector layer.)

**AWS Glue.** Its catalog federation is also partner-specific
(Snowflake/Databricks/Redshift); no generic-IRC connection type in the API. So
Databricks's "we do what Glue does" is, on inspection, defensible — *both* are
partner-gated. It's not that Databricks is uniquely closed; it's that the
**managed-warehouse federation features as a class are gated**, while the
**query engines are open**.

**BigQuery.** No remote-IRC *consumer* at all — BigLake metastore is Google's
*own* catalog that BigQuery reads; it doesn't attach arbitrary external IRC
catalogs.

## Why a "public credential" can't rescue it

A natural idea: don't fake the credential — publish a *real, read-only* one for
everyone to use. It doesn't work, and the reason is illuminating: **the blocker
isn't secrecy, it's a protocol mismatch.**

The catalog endpoint *is* the object store, so authenticating to it means
speaking the object store's protocol (S3 SigV4, or GCS's `Bearer <Google
token>`). But the warehouse's IRC connector only knows how to send *IRC-server*
auth — `BEARER`, `OAUTH` (token exchange), or `SigV4-for-AWS-services` (Glue /
API Gateway, never plain S3). There's no credential — secret or public — you can
hand Snowflake that it will *use correctly* against raw S3/GCS. The two
vocabularies don't intersect. Publishing the credential doesn't teach the
warehouse to sign an S3 GET.

## The bridge: a permissive front that absorbs the auth

If the warehouse insists on sending an `Authorization` header and the object
store insists on rejecting it, put something between them that **ignores** it:

**CloudFront over the S3 bucket** with the managed *CachingOptimized* policy
doesn't forward `Authorization` to the origin — it serves the cached public
object. Config-only, no server. Point Snowflake at the CloudFront URL with a
dummy bearer, add an external volume for the data (`ALLOW_WRITES=FALSE` +
a scoped read-only IAM role), and **Snowflake reads the catalog end-to-end** —
`COUNT(*) = 10000`, bbox predicate `= 196`, real rows. The catalog stays static
files; the CDN absorbs the auth the warehouse demands.

**A shared Cloudflare Worker** ([`portolan-proxy/`](portolan-proxy/)) does it one
better. It's stateless and path-encoded — `/{gcs|s3}/<bucket>/<prefix>` maps to
the origin — so *no creator has to provision anything*; they just publish static
files and use the shared proxy URL. It drops `Authorization`, and crucially it
**serves a fake `POST /v1/oauth/tokens`** — the thing a dumb CDN can't, the piece
OAuth clients need. It only relays the tiny catalog JSON (engines read the data
directly from storage), so it's near-free. Snowflake consumes a catalog through
it end-to-end too.

What the proxy *can't* fix: the data layer. Engines read the actual parquet via
their own storage access (Snowflake's external volume, Databricks's UC external
location), and that governance is unavoidable. And Oracle stayed out of reach
even with the fake token endpoint — its `MOUNT` defers the connection and never
actually called the proxy in our tests, and its Iceberg reader has a separate,
deeper bug.

## Two ways to reach a table — and why it matters

There's a humbler access mode that's more portable than the catalog: **point an
engine at a single table's `metadata.json` URL**, no catalog at all.

- **BigQuery** does this with zero ceremony:
  `CREATE EXTERNAL TABLE … OPTIONS(format='ICEBERG', uris=['…/metadata.json'])`.
  It uses the query connection's storage access — no credential dance. (It reads
  our V2 tables with file pruning; it rejects V3 geometry, but that's the
  geometry story, not the catalog story.)
- **Databricks** has *no* lightweight equivalent. `CREATE TABLE … USING ICEBERG
  LOCATION '<path>'` is accepted syntactically but requires a Unity Catalog
  *external location* (a registered storage credential) before it'll touch any
  path. There's no `iceberg.\`url\`` reader and `read_files` doesn't do Iceberg.
  *Everything* external routes through UC governance — there's no anonymous door
  anywhere.

The nice property of a Portolan catalog: its `loadTable` response literally hands
back each table's `metadata.json` URL — so even an engine that can't consume the
*catalog* can often read the *tables* via that URL. The catalog is discovery and
convenience; the per-table pointer is the universal fallback that degrades
gracefully to the widest set of engines.

| Engine | Consume the IRC catalog | Read a table by `metadata.json` |
|---|---|---|
| DuckDB / Trino / Spark / PyIceberg | ✅ (anonymous / token) | ✅ |
| Snowflake | ✅ via a CDN/Worker front (+ external volume) | ✅ (managed paths) |
| BigQuery | ❌ (no remote-IRC consumer) | ✅ credential-less (`uris=[…]`) |
| Databricks | ❌ (no generic connector, by design) | ❌ (UC external location required) |
| AWS Glue | ❌ (partner-gated federation) | n/a |
| Oracle ADB | ❌ (needs OAuth handshake; + reader bug) | ❌ (reader bug) |

## A real-world foil: Google's public BigLake catalog

Google publishes public Iceberg datasets via a real IRC endpoint
(`https://biglake.googleapis.com/iceberg/v1/restcatalog`). It's the closest
production analog — and it makes the trade-off concrete. It is **not anonymous**:
consuming it requires a Google OAuth2 token *and* a mandatory
`X-Goog-User-Project` quota header. PyIceberg can supply both (we read the NYC
taxi tables end-to-end). **DuckDB can't** — its IRC client doesn't send custom
headers, so it gets a `403`.

| | **Portolan (static)** | **Google BigLake (public)** |
|---|---|---|
| Architecture | static files, serverless | managed server |
| Consume auth | none (anonymous) for open clients | Google token **+** quota header (mandatory) |
| DuckDB | ✅ | ❌ (can't send the header) |
| PyIceberg / Spark | ✅ | ✅ |
| Governance / quota | none | managed, quota-tracked |

Even Google's flagship "public Iceberg" is gated behind a Google project.
Portolan trades a server's governance for radical openness — it's the only one
of these a credential-less DuckDB can read.

## The takeaway

**"Open Iceberg" is, today, more aspirational than real — at the catalog layer.**
The format is genuinely open and the data is reachable; what's gated is the
*door*:

- The **query-engine ecosystem** (DuckDB, Trino, Spark, PyIceberg) treats
  conformance as sufficient — point it at a spec-compliant endpoint and it
  works. This is what "open" should mean.
- The **managed warehouses** (Snowflake, Databricks, Glue, Oracle) require an
  auth/governance relationship the open path can't satisfy — and a public bucket
  *can't even present a credential they'll accept*. Conformance isn't enough;
  certification or a commercial relationship is.

If you want to publish open Iceberg today, the practical recipe is:

1. **Publish the data + a static catalog on a bucket** (S3 widest-compatible).
   Expose per-table `metadata.json` URLs as the universal fallback.
2. **Front the catalog with a permissive CDN or a tiny edge proxy** if you need
   the warehouses — it absorbs the auth they insist on, and an edge function can
   even serve the OAuth handshake. The catalog stays static.
3. **Accept that the data layer is per-engine** — external volumes, UC external
   locations, storage credentials. The proxy fixes the catalog door, not the
   storage governance.

The whole thing is reproducible — the static-catalog generator, the Worker, and
the per-engine probes are in the repo, and the catalogs are live and public.

---

**Repo**: [jatorre/iceberg-geo-testbed](https://github.com/jatorre/iceberg-geo-testbed)
· **Status matrix**: [STATUS_CATALOG.md](./STATUS_CATALOG.md)
· **The proxy**: [portolan-proxy/](portolan-proxy/)
· **Companion post** (geospatial on Iceberg): [BLOG_GEO.md](./BLOG_GEO.md)
