# Apache Polaris — reference Iceberg catalog (sanity check)

Status as of **2026-05-24.** Apache Polaris `latest` running in Docker on
a GCE `e2-small` VM at `136.112.253.147:8181`, in-memory backend,
realm=`POLARIS` with bootstrap principal `root`/`s3cr3t`. This is *not*
an engine row in the matrix — it's a known-spec-compliant catalog we
used as an oracle to validate our hand-written metadata.

## Why it's here

When Oracle ADB rejected our metadata with a generic `ORA-20000: Iceberg
parameter error`, the question became: *is our metadata actually
spec-compliant?* We had circumstantial evidence (Sedona/Iceberg-Spark
reads it to L3) but no direct validator. Polaris — Snowflake-donated,
the de-facto reference Iceberg REST catalog — fills that role.

## Findings

| Fixture | Polaris registration | Verdict on our metadata |
|---|---|---|
| `v2_flat_columns` | **200 OK** | V2 metadata is **spec-compliant**. The Oracle rejection is Oracle's bug, not ours. |
| `v2_bbox_struct`  | **200 OK** | same |
| `v3_geometry` (before fix) | **400** — `Cannot parse missing long: next-row-id` | Real gap: pyiceberg 0.11.1 doesn't emit V3-required fields. |
| `v3_geometry` (after fix) | **200 OK** | Patched `_static_catalog.py` to emit `next-row-id: 0` + `row-lineage: false` when `format_version_in_metadata=3`. V3 metadata now passes the reference catalog. |

So our V2 metadata was always compliant; our V3 metadata had one real
spec gap that no other engine we tested would have caught (they all
either accepted V3 syntactically up to the geometry-type rejection, or
required catalog mediation we hadn't set up).

## What Polaris did *not* unblock

We hoped Polaris could be the catalog through which Oracle and
Databricks read our metadata (since both of those don't support direct
path-based metadata reads cleanly). Result:

- **Oracle**: rejects self-hosted Polaris endpoints. Its REST-catalog
  reader prepends `iceberg:` to `file_uri_list` and yells about "Invalid
  URL"; on a bare HTTP REST endpoint it returns "Unsupported object
  store URI". The integration only seems to recognize specific cloud
  endpoints (Snowflake-Polaris-hosted, AWS Glue, etc.) — not a generic
  Iceberg REST endpoint at a raw IP.
- **Databricks**: `CREATE CONNECTION TYPE iceberg` errors with
  `CONNECTION_TYPE_NOT_SUPPORTED`. The supported foreign-catalog types
  for Iceberg are GLUE, Unity, Snowflake Horizon — no generic
  Iceberg-REST connector yet.

So Polaris validates the metadata but isn't usable as a generic-bridge
for these two engines, at least with their current public connectors.

## Files

- `_setup.py` — Python script: OAuth against Polaris, create the
  `testbed` catalog backed by `gs://cartobq-iceberg-geo-testbed/`, create
  one namespace per fixture, register each table by `metadata-location`.

## Spinning it up yourself

```bash
# Provision the VM (e2-small, $9/mo)
gcloud compute instances create iceberg-polaris-testbed \
  --project=cartobq --zone=us-central1-a \
  --machine-type=e2-small \
  --image-family=ubuntu-2204-lts --image-project=ubuntu-os-cloud \
  --tags=polaris-server \
  --metadata-from-file startup-script=engines/polaris/_startup.sh

gcloud compute firewall-rules create allow-polaris \
  --rules=tcp:8181,tcp:8182 --target-tags=polaris-server \
  --source-ranges=0.0.0.0/0

# Wait ~2 minutes for the startup script to install docker + run polaris,
# then:
python engines/polaris/_setup.py
```

(`_startup.sh` is also committed — the same content the gcloud command
streams into `--metadata-from-file`.)

## Tear-down

```bash
gcloud compute instances delete iceberg-polaris-testbed --zone=us-central1-a
gcloud compute firewall-rules delete allow-polaris
```

## Bonus: the real take-away

Polaris is the cheapest spec-validator we have. Any time we change
`_static_catalog.py` or upgrade pyiceberg, the right sanity check is:
re-run `engines/polaris/_setup.py` and make sure all three fixtures
still return 200 on registration. That catches spec drift even when the
permissive engines don't complain.
