# Snowflake engine runner

Status as of **2026-05-26.** Account `KQ34251` (Snowflake `10.19.100`,
hosted on `GCP_EUROPE_WEST2`).

## TL;DR

**V2 fixtures: L3 on Snowflake** for `v2_flat_columns`, `v2_bbox_struct`,
and `v2_geo_convention` (the GeoIceberg V2 spec reference impl). Snowflake
serves `COUNT(*)` with the bbox predicate from manifest `record_count`
directly — even stronger than file-level pruning.

**V3 fixture: blocked** with a specific and actionable error
(`incomplete state — Please complete the upgrade`). Root cause: pyiceberg
0.11.1 writes V2-format manifest avros while our metadata.json claims
V3; Snowflake's V3 reader catches the inconsistency. Other engines
(Polaris, Iceberg-Spark) accept this hybrid.

Run `python engines/snowflake/run.py` to reproduce.

## The 091369 IAM trap (public service announcement)

If you're hitting `091369: Query needs to be retried to setup external
volume for Iceberg table` on Snowflake, this section is the cause.

The error is **misleading**. Retries don't help, and the underlying
problem isn't logged anywhere obvious:

- `SYSTEM$VERIFY_EXTERNAL_VOLUME` returns `success: true` with all four
  sub-checks (`writeResult/readResult/listResult/deleteResult`) PASSED.
  But `VERIFY` only tests **object-level** permissions
  (`storage.objects.*`).
- `SNOWFLAKE.MONITORING.ICEBERG_ACCESS_ERRORS` returns zero rows.

The actual missing permission is **`storage.buckets.get`** — a
*bucket-level* permission that `roles/storage.objectAdmin` does **not**
grant. Snowflake's region-resolution step fails silently when it can't
read bucket metadata, returns the wrapper "needs to be retried" error,
and (critically) doesn't log the underlying 403 anywhere visible.

**The fix on GCS** is one IAM grant. We chose
`roles/storage.legacyBucketReader` as the narrowest built-in role
that adds `storage.buckets.get`:

```bash
gsutil iam ch \
  serviceAccount:<SNOWFLAKE_GCP_SA>:legacyBucketReader \
  gs://<your-bucket>/
```

You can find `<SNOWFLAKE_GCP_SA>` in the `DESC EXTERNAL VOLUME` output
under `STORAGE_GCP_SERVICE_ACCOUNT`. The SA name has the form
`<random>@gcpeuropewest2-1-<id>.iam.gserviceaccount.com` (or the
equivalent for your region). Custom roles with just the five permissions
listed in Snowflake's docs are equivalently safe and tighter; the
legacy role is just lower-friction.

Confirmed by Snowflake Support in May 2026. Snowflake's
`SYSTEM$VERIFY_EXTERNAL_VOLUME` ideally would test this too, but
doesn't yet — worth a feedback request to them.

## Test results

After the IAM fix, all three V2 fixtures register and query correctly:

| Fixture | Level | Notes |
|---|---|---|
| `v2_flat_columns` | **L3** | `bytes_scanned=0` because Snowflake answers `COUNT(*) WHERE bbox-predicate` from manifest `record_count` |
| `v2_bbox_struct`  | **L3** | Predicate syntax for struct fields: `bbox:xmin::FLOAT <= ...` (Snowflake variant-access notation, vs. dot notation in other engines) |
| `v2_geo_convention` | **L3** | The GeoIceberg V2 spec reference impl works end-to-end on Snowflake |
| `v3_geometry` | **L0** | `Iceberg table 'V3_GEOMETRY' is V3 but is in an incomplete state. Please complete the upgrade before creating an iceberg table.` |

## The V3 "incomplete state" — our writer side, not Snowflake's

Important nuance: the rejection on our V3 fixture is **us producing
non-spec-compliant V3**, not a Snowflake capability gap.

Our V3 metadata.json claims `format-version: 3` and uses the V3
`geometry(OGC:CRS84)` column type. But the **manifest avro** is V2
format — pyiceberg 0.11.1 hardcodes `format_version=2` in
`write_manifest()`. This mismatch (V3-claiming metadata.json pointed at
V2 manifest avro) is what Snowflake flags as "incomplete state".

Other tools we tested (Polaris, Iceberg-Spark, DuckDB) are *more
permissive* and accept the hybrid — which is what let us probe their
V3 paths and find the type-recognition gap in DuckDB and the type
rejection in BigQuery. Snowflake's V3 reader is stricter and catches
the inconsistency before doing any geometry-specific evaluation.

**So we have NOT actually tested Snowflake's claimed `full` V3 geometry
support.** Their reader never got to the geometry-column part of the
evaluation; our writer never got past the V3 manifest spec.

Ways to actually test Snowflake's V3 geometry:

1. **Snowflake-managed V3.** Create a Snowflake-managed Iceberg V3
   table with a `GEOMETRY` column (`CREATE ICEBERG TABLE ... USING
   ICEBERG TBLPROPERTIES('ICEBERG_VERSION'='3')`), insert data, run
   our spatial probes. Tests Snowflake's V3 implementation directly
   with Snowflake doing the writing.
2. **Third-party V3 writer.** Iceberg-Spark can't (UDT mapper gap
   documented elsewhere); pyiceberg V3 writer is incomplete (tracked
   at [iceberg-python#1818](https://github.com/apache/iceberg-python/issues/1818));
   Wherobots' Sedona fork might — worth checking.
3. **Hand-write V3 manifest avro.** Extend `_static_catalog.py` to
   emit V3-spec manifest avro. Real implementation work; effectively
   filling in pyiceberg's missing feature ourselves.

Path 1 is the fastest practical test if all we want is to know whether
Snowflake's V3 geometry works end-to-end. It doesn't test cross-engine
interop on V3, but cross-engine V3 interop is moot today anyway given
no other engine reads V3 geometry past type-recognition.

## Files

- `_creds.py` — credentials loader (file backend at
  `~/.config/iceberg-geo-testbed/snowflake.txt` or gcloud secret backend).
- `_discover.py` — read-only state probe (account info, roles,
  external volumes, catalog integrations, privilege probe).
- `_provision.py` — idempotent setup: external volume +
  `CATALOG INTEGRATION ICEBERG_CAT_FRESH` + database `TESTBED`. Reusable.
- `run.py` — full L0–L4 probe against all four fixtures.

## Reproducing

```bash
# One-time wallet/creds setup — see _creds.py for the file format
mkdir -p ~/.config/iceberg-geo-testbed
cat > ~/.config/iceberg-geo-testbed/snowflake.txt <<EOF
https://<account>.snowflakecomputing.com/console/login
<USERNAME>
<password>
EOF
chmod 600 ~/.config/iceberg-geo-testbed/snowflake.txt

# Provision the external volume + catalog integration + database
python engines/snowflake/_provision.py

# IAM: grant storage.buckets.get on the bucket — see _provision.py output
# for the storage_gcp_service_account name to grant
gsutil iam ch serviceAccount:<SA>:legacyBucketReader gs://<bucket>/

# Run the probe
python engines/snowflake/run.py
```
