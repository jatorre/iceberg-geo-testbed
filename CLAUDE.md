# CLAUDE.md — operational context & handoff

Context for working in this repo. Start with [README.md](./README.md) for the
two-track overview; this file is the operational layer: live cloud resources,
where credentials live, how to re-run things, and the gotchas that cost real
time the first time around.

> **This file is committed (public repo).** It contains resource *names*,
> *purposes*, and *public* URLs only — **never** secret values, tokens, keys, or
> internal account numbers. Credentials are referenced by their on-disk
> location; secrets stay in `~/.config/iceberg-geo-testbed/` and out of git.

## What this repo is

A cross-engine Apache Iceberg testbed, organized as two tracks:

- **Geo track** — geospatial support on Iceberg (V3 `geometry`/`geography`,
  GeoParquet 2.0, the GeoIceberg V2 convention). See `SPEC.md`, `STATUS_V2.md`,
  `STATUS_V3.md`, `BLOG_GEO.md`.
- **Catalog track** — can a public Iceberg catalog be consumed by arbitrary
  engines? See `STATUS_CATALOG.md`, `portolan-proxy/`, `BLOG_CATALOG.md`.

Shared core: `testbed/` (fixtures + the static-catalog generator) and
`engines/` (per-engine runners). Status matrices in the `STATUS_*` files are the
source of truth for findings; the blogs are the narrative.

## Credentials — all under `~/.config/iceberg-geo-testbed/` (chmod 600)

Never commit these; never paste secret values into chat or files. Move any
creds the user drops onto the Desktop into this dir and `chmod 600` them
(macOS TCC may block the Desktop folder entirely — write to `~/.config/...` or
`/private/tmp` instead of fighting it).

| File / dir | What | Notes |
|---|---|---|
| `snowflake.txt` | Snowflake URL / user / password (3 lines) | personal trial account, ACCOUNTADMIN. Loader: `engines/snowflake/_creds.py`. |
| `databricks-sandbox.txt` | host / http_path / token (3 lines) | Databricks **Free Edition** sandbox (you're metastore admin there). |
| `aws-credentials.txt` | `export AWS_*` lines | **Temporary STS creds — expire in hours.** When expired, ask the user to refresh (have them write fresh creds to this path; don't expect Desktop access to work). |
| `oracle-wallet/` | extracted ADB wallet | TNS service `acmefreetier_high`. Loader: `engines/oracle/_creds.py`. |
| `horizon-keys/` | RSA keypair for Snowflake Horizon JWT | used by `engines/snowflake/_horizon_jwt.py`. |

Other auth:
- **gcloud**: `jatorre@cartodb.com`, project `cartobq`. Token expires; re-auth
  with `gcloud auth login` (the user runs it — interactive).
- **Cloudflare (wrangler)**: OAuth login stored by wrangler (not in this repo).
  `wrangler login` is interactive (user runs it). `wrangler` lives under nvm
  (`~/.nvm/.../bin`) — export that on PATH for non-interactive shells.
- **Snowflake / AWS / Cloudflare account identifiers** are intentionally *not*
  written here. Get them from the creds files / `DESC` output / `wrangler
  whoami` at runtime.

## Live cloud resources (kept as reference — do not tear down)

The user's standing instruction: **keep these as reference for others; they're
cheap.** Don't delete them without being asked.

**GCS** (project `cartobq`, public-read):
- `gs://cartobq-iceberg-geo-testbed/` — public per-table fixtures
  (`{v2_flat_columns,v2_bbox_struct,v2_geo_convention,v3_geometry,v3_geometry_lineage}/`)
  **plus** the static IRC catalog under `catalog/`
  (base URI `https://storage.googleapis.com/cartobq-iceberg-geo-testbed/catalog`).
- `gs://cartobq-iceberg-geo-testbed-eu/` — Snowflake-managed Iceberg data
  (managed V2 + V3 tables Snowflake wrote; public-read).

**AWS** (CARTO AWS account, region `us-east-1`):
- S3 bucket `carto-iceberg-geo-testbed-public` — **public** static IRC catalog
  (`catalog/`, `s3://` data paths via `--target=s3native`). Bucket-level public
  access is enabled on *this* bucket only (account has no account-level BPA).
- CloudFront distribution over that bucket (domain
  `d2q30u72s40ftj.cloudfront.net`) — *CachingOptimized* policy, doesn't forward
  `Authorization`. The permissive front Snowflake reads through.
- IAM role `snowflake-portolan-extvol` — read-only on the public bucket; trusts
  Snowflake's external-volume principal with an external ID. **The external ID
  rotates on `CREATE OR REPLACE EXTERNAL VOLUME`** — re-`DESC EXTERNAL VOLUME`
  and `update-assume-role-policy` if you recreate the volume.

**Cloudflare** (account `jatorre@cartodb.com`):
- Worker `portolan-irc-proxy` at `portolan-irc-proxy.carto-portolan.workers.dev`
  (subdomain `carto-portolan`). Source in `portolan-proxy/`; `wrangler deploy`.

**Snowflake** (personal trial, GCP `EUROPE_WEST2`, DB `TESTBED` schema `PUBLIC2`):
- `managed_v2_geo`, `managed_v3_geo` — Snowflake-managed Iceberg (the working V3
  geometry reference; data in the `-eu` GCS bucket).
- `v2_flat_columns` / `v2_bbox_struct` / `v2_geo_convention` — unmanaged V2.
- `portolan_irc` (CloudFront front) / `portolan_worker` (Worker front) — IRC
  catalog integrations; `portolan_s3_vol` external volume (`ALLOW_WRITES=FALSE`);
  `portolan_v2_flat` / `portolan_v2_worker` tables.
- External volume `ICEBERG_VOL_FRESH` + catalog integration for the unmanaged
  GCS path. Warehouse `COMPUTE_WH`.

**Oracle ADB** 26ai (Always Free), **Databricks** (Free Edition sandbox + a
shared CARTO corp metastore where you *lack* `CREATE CONNECTION`).

**Apache Polaris** — the GCE VM is **gone** (all instances terminated). Redeploy
with `engines/polaris/_setup.py` + `_startup.sh` if you need it as a spec
validator again.

## How to run

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
brew install duckdb                                   # ≥ 1.5.3

# fixtures (deterministic; California probe = 196 rows)
python -m testbed.v2_flat_columns        # + v2_bbox_struct, v2_geo_convention, v3_geometry
python engines/duckdb/run.py             # local
python engines/bigquery/run.py           # needs gcloud

# static IRC catalog
python -m testbed.static_rest_catalog --target={gcs|s3|s3native} --publish

# cloud engines: each has its own _creds.py + run/probe scripts under engines/*/
```

Engine runners load creds from `~/.config/iceberg-geo-testbed/`. For AWS/Snowflake
steps, source `aws-credentials.txt` and select the Snowflake profile as the
engine scripts do.

## Gotchas (each cost real time)

- **Snowflake external volume on GCS needs `storage.buckets.get`** — grant the
  Snowflake GCS SA `roles/storage.legacyBucketReader` *in addition to*
  object-level read, or `CREATE ICEBERG TABLE` fails with the misleading
  `091369: Query needs to be retried`. `VERIFY_EXTERNAL_VOLUME` doesn't catch it.
- **Snowflake reads a static catalog only via a permissive front.** Its
  `ICEBERG_REST` integration mandates `REST_AUTHENTICATION`; raw S3/GCS reject a
  dummy bearer (S3 `400`, GCS `401`). SigV4 in Snowflake targets Glue/API-Gateway,
  not plain S3. The CloudFront/Worker front that drops `Authorization` is the
  bridge. Data still needs an external volume (`ALLOW_WRITES=FALSE` for read-only,
  else Snowflake's write-test fails).
- **Snowflake metadata wants `s3://` data paths** for the external volume to map
  (use `--target=s3native`); CloudFront caches, so **invalidate** after a
  re-publish (`aws cloudfront create-invalidation`).
- **Databricks Free Edition has no Workload Identity Federation** — keyless GCP
  storage credentials are blocked; only an SA-key path works. Its direct
  Iceberg read also rejects the `gcs://` scheme (wants `gs://`). Everything
  external routes through Unity Catalog governance (external locations).
- **Oracle ADB** — its Iceberg reader fails `ORA-20000: Failed to generate
  column list` on our tables regardless of storage/producer/metrics (a
  reader-side bug, not our metadata). Its AWS credential rejects temporary STS
  session tokens (`ORA-20403`) — needs long-lived IAM keys. `MOUNT_ICEBERG`
  defers the catalog connection (config-only) so "MOUNT OK" ≠ "authenticated".
- **Cloudflare Worker** — a fresh workers.dev subdomain takes minutes to
  provision TLS; Cloudflare may `403` (`error 1010`) a `python-urllib`
  user-agent — use a normal UA for tests (real clients are fine).
- **pyiceberg 0.11.1** can't write V3 manifests — `testbed/_static_catalog.py`
  subclasses the V2 manifest writers to emit V3 (with `first_row_id`, etc.).
- **Determinism** — `testbed/common.py:stable_seed` (sha256) makes fixture
  parquet byte-identical across processes; the probe is always 196 rows.

## Conventions & user preferences

- **Don't cite private sources or unreleased dates.** For roadmap items learned
  through private channels, write "likely coming soon" — **no source, no date**
  (e.g. Databricks geo-in-Iceberg).
- **Lock down credential files** (`chmod 600`) and keep them out of git and out
  of chat transcripts.
- **Keep the test cloud resources** — they're the public reference others test
  against.
- Commits: the user generally wants doc/work changes committed and pushed when a
  unit of work is done; confirm before destructive or shared-state-risky actions.
- `data/`, `spark-warehouse/`, `portolan-proxy/.wrangler/` are gitignored
  (generated / local state).

## Open threads (not yet done)

- **duckdb-iceberg#1002** — the manifest geometry-bound deserializer; DuckDB
  maintainer acknowledged. Re-test when a branch lands → would flip DuckDB V3
  to L3.
- **Databricks geo-in-Iceberg** — re-test periodically (works in Delta, not yet
  in the Iceberg-compat writer).
- **Microsoft Fabric** — untested (no access); the one empty cell in the catalog
  matrix.
- **Snowflake V3 unmanaged read** — broadly non-functional; worth a support
  follow-up on what an external V3 fixture must contain.
