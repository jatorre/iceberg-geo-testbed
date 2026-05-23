# Snowflake engine runner — blocked

Status as of **2026-05-23.** Two account states tried; both blocked.

## Account A — `SXA81489` (CARTO dev, shared)

Discovery only. The available role (`TEST_ROLE`) cannot
`CREATE EXTERNAL VOLUME` — the privilege is account-scoped and requires
`ACCOUNTADMIN`. No external volumes exist for us to reuse. The one
existing storage integration is wired for EXTERNAL_STAGE (file loading),
not Iceberg external volumes.

To unblock this account, ACCOUNTADMIN would need to:
- Create an external volume backed by S3 (the account is on AWS_US_EAST_1)
- Grant USAGE on it to `TEST_ROLE`

## Account B — `KJEIDXA-IK05112` (personal trial, GCP_EUROPE_WEST2)

We have `ACCOUNTADMIN` here. Got further but hit a different wall.

What works:
- `CREATE EXTERNAL VOLUME` against either the US public bucket or a
  same-region (EU) public bucket.
- `SYSTEM$VERIFY_EXTERNAL_VOLUME(...)` returns
  `success: true` with `writeResult/readResult/listResult/deleteResult = PASSED`
  once Snowflake's GCP service account
  (`nkxeengujz@gcpeuropewest2-1-4e2d.iam.gserviceaccount.com`) is granted
  `objectAdmin` on the bucket. (Public-read alone isn't sufficient for verify
  — Snowflake's check requires write/list/delete to all pass.)
- `CREATE CATALOG INTEGRATION ICEBERG_FILES CATALOG_SOURCE = OBJECT_STORE
  TABLE_FORMAT = ICEBERG ENABLED = TRUE` succeeds.

What does **not** work:
- **Any** `CREATE ICEBERG TABLE` (managed *or* unmanaged) against the
  verified external volume fails with:

      091369 (55000): Query needs to be retried to setup external volume
      for Iceberg table <NAME>. Please retry the query.

  The error is misleading — retries don't help. We exhaustively tested:
  retry-after-delay (0s, 5s, 15s, 30s), `AUTO_REFRESH=FALSE`, full nuke +
  rebuild of the volume + catalog + database with fresh names,
  `CATALOG='SNOWFLAKE'` (managed) vs `CATALOG='ICEBERG_FILES'` (unmanaged),
  cross-region US bucket vs same-region EU bucket. The error is identical
  in every case.

  We confirmed via `SNOWFLAKE.MONITORING.ICEBERG_ACCESS_ERRORS` that
  Snowflake is **not** logging any cloud-storage error for the failing
  CREATE — zero rows for `ICEBERG_VOL_FRESH`. So 091369 fires upstream of
  the GCS call, in Snowflake's internal Iceberg-table provisioning state
  machine. (For comparison: an earlier attempt against `ICEBERG_TESTBED_VOLUME`
  did log a real `403 Forbidden: storage.objects.create` from GCS into the
  same view — that's how we discovered Snowflake needs `objectAdmin` on
  the bucket even for "read-only" Iceberg tables.)

  Since the error reproduces for **Snowflake-managed Iceberg too**, the
  blocker is not in our hand-written metadata. With `VERIFY_EXTERNAL_VOLUME`
  passing and zero cloud-side errors, the next step is a Snowflake support
  ticket against the account-side Iceberg provisioning pipeline.

## What works for someone with a non-blocked account

If you have a Snowflake account where `CREATE ICEBERG TABLE` succeeds,
everything is in place to register and probe our fixtures. Run
`engines/snowflake/_provision.py` first to set up the catalog integration
+ external volume against the public bucket, then:

```sql
CREATE OR REPLACE ICEBERG TABLE v2_flat_columns
  EXTERNAL_VOLUME = 'ICEBERG_TESTBED_VOLUME'
  CATALOG = 'ICEBERG_FILES'
  METADATA_FILE_PATH = 'v2_flat_columns/metadata/v1.metadata.json';

SELECT COUNT(*) FROM v2_flat_columns
  WHERE xmin <= -118 AND xmax >= -125 AND ymin <= 40 AND ymax >= 37;
-- expect 196 rows. Compare query profile's partitions_scanned to confirm
-- file-level pruning to 1/10 files.
```

## Files in this directory

- `_creds.py` — credentials loader with two backends: a 3-line text file at
  `~/.config/iceberg-geo-testbed/snowflake.txt` (URL / user / password)
  for personal accounts, or the CARTO `carto-dev-database-credentials`
  gcloud secret. Picks file backend first if the file exists.
- `_discover.py` — read-only one-shot discovery probe: account info,
  available roles, existing iceberg infra, privilege probe.
- `_provision.py` — idempotent setup: catalog integration + external volume
  pointing at the public testbed bucket + `ICEBERG_TESTBED` database.

`run.py` (TODO) — once the 091369 blocker is resolved on a working account,
pattern after `engines/bigquery/run.py`: register all three fixtures, run
probes, report the L0–L4 ladder per fixture.
