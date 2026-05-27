# portolan-irc-proxy — a shared, stateless front for static Iceberg REST catalogs

Part of the **catalog track** (see [../STATUS_CATALOG.md](../STATUS_CATALOG.md)
and [../BLOG_CATALOG.md](../BLOG_CATALOG.md)).

A static Portolan catalog (plain JSON on a bucket) is consumable directly by
open IRC clients (DuckDB, Trino, Spark, PyIceberg). The managed warehouses,
though, **mandate an `Authorization` header** that a raw object store rejects —
so they can't read a public static catalog directly. This Cloudflare Worker is
the bridge: one shared, stateless edge proxy that **absorbs the auth header** and
**serves a fake OAuth token endpoint**, so a warehouse's mandatory-but-meaningless
auth handshake succeeds against a public catalog.

It does two things a dumb CDN (e.g. CloudFront) can't:
1. Serves `POST …/v1/oauth/tokens` → a throwaway token (for OAuth clients).
2. Is multi-tenant and stateless — *any* Portolan catalog works through it with
   no per-creator setup.

## How it works

- **Stateless path-encoded origins** — no registry, no `create` step. The proxy
  URL *is* derived from the catalog's bucket:
  - `https://<worker>/gcs/<bucket>/<prefix>` → `https://storage.googleapis.com/<bucket>/<prefix>`
  - `https://<worker>/s3/<region>/<bucket>/<prefix>` → `https://s3.<region>.amazonaws.com/<bucket>/<prefix>`
  A creator publishes their static catalog and uses the derived proxy URL as
  their warehouse's `CATALOG_URI`.
- **Absorbs `Authorization`** — fetches the origin object anonymously, never
  forwarding the inbound header (which the object store would reject).
- **Fake `/v1/oauth/tokens`** — returns `{access_token, token_type, expires_in}`.
- **Catalog-only** — it relays just the small IRC JSON. Engines read the actual
  data files directly from storage (external volume / UC external location), so
  the proxy carries trivial bandwidth.
- **SSRF guard** — only `gcs`/`s3` schemes, GET-only proxying, and only `/v1/`
  catalog paths (data paths are never fetched through the proxy).

## Deployed instance

```
https://portolan-irc-proxy.carto-portolan.workers.dev
```

Example (Snowflake, against the public S3 catalog):

```
CATALOG_URI = https://portolan-irc-proxy.carto-portolan.workers.dev/s3/us-east-1/carto-iceberg-geo-testbed-public/catalog
```

Verified: Snowflake creates a `CATALOG INTEGRATION` (`CATALOG_API_TYPE=PUBLIC`,
dummy `BEARER`) against this URL and reads a table end-to-end (`COUNT=10000`,
bbox `=196`) with an external volume for the data.

## Deploy / update

```bash
npm install -g wrangler      # if not present
wrangler login               # OAuth; needs Workers scope
cd portolan-proxy
wrangler deploy
```

(First deploy on a fresh account needs a workers.dev subdomain registered once.)

## Caveats

- **It's a shared service.** One tiny stateless Worker for the whole ecosystem
  (like a public CORS proxy), but it is a dependency — not "100% serverless per
  creator." For production you'd run your own.
- **It bridges catalog auth only.** The data layer still needs the engine's
  normal storage access (Snowflake external volume, etc.).
- **Oracle stays out of reach** even with the token endpoint — its `MOUNT`
  defers the connection and it has a separate reader bug. See STATUS_CATALOG.md.
