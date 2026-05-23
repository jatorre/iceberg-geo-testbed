# BigQuery / BigLake engine runner

Status as of **2026-05-23** — BigQuery version: whatever was live in
`us-multi-region` on that date.

## Result on this date

| Fixture | Highest level reached |
|---|---|
| `v2_flat_columns` | **L3** — manifest-level file pruning works |
| `v2_bbox_struct`  | **L3** — manifest-level file pruning works (struct fields prune too) |
| `v3_geometry`     | **L0** — fails at table registration: `Unknown Iceberg type "geometry(OGC:CRS84)"` |

Run `python engines/bigquery/run.py` to reproduce.

## Inferring pruning from `total_bytes_processed`

We don't get a "files scanned" field in `INFORMATION_SCHEMA.JOBS_BY_PROJECT`,
but the byte count is a clean proxy for our synthetic fixtures:

- Each fixture has 10 files × 1000 rows. Each row is fixed-width.
- For a predicate touching N double columns:
  - 1 file = `1000 × 8 × N` bytes uncompressed
  - All 10 = `1000 × 8 × N × 10` bytes
- The California-window probe hits 4 columns. So the expected pruned size is
  `32,000` bytes and the all-files size is `320,000`. The measured 32,000 is
  unambiguously "1 file scanned".

This works because `total_bytes_processed` for BigLake external tables
counts the bytes read from cloud storage *after* column projection and
manifest-level file selection. (Native-table billing also reports logical
column-times-rows bytes, which is the same arithmetic.)

## The V3 geometry block

```
Unknown Iceberg type "geometry(OGC:CRS84)".
File: bigstore/cartobq-iceberg-geo-testbed/v3_geometry/metadata/v1.metadata.json
```

BigQuery's BigLake external-Iceberg reader does not (as of this date)
recognize the V3 `geometry(<CRS>)` type token in `metadata.json`. The table
fails to register — we don't even get to ask about pruning. The path
forward is upstream: Google has announced V3 geo support on the roadmap;
this README should flip to a non-L0 row once it lands.

## What was set up

- GCS bucket `gs://cartobq-iceberg-geo-testbed/` (public, in `cartobq`)
  contains the three fixtures' data + metadata at:
    ```
    gs://cartobq-iceberg-geo-testbed/<table>/data/*.parquet
    gs://cartobq-iceberg-geo-testbed/<table>/metadata/v1.metadata.json
    gs://cartobq-iceberg-geo-testbed/<table>/metadata/*manifest*.avro
    ```
- BQ dataset `cartobq.iceberg_geo_testbed` holds the three external tables.
- BQ connection `cartobq.us.iceberg_connection` provides the GCS read
  credentials. The connection's service account
  (`bqcx-1008945414091-59dh@gcp-sa-bigquery-condel.iam.gserviceaccount.com`)
  has `objectViewer` on the bucket (in addition to the public-read ACL).

The bucket is publicly readable — anyone with a BigQuery project can repro
this with their own connection:

```sql
CREATE OR REPLACE EXTERNAL TABLE myproj.mydataset.v2_flat_columns
WITH CONNECTION `myproj.us.my_connection`
OPTIONS (
  format = 'ICEBERG',
  uris = ['gs://cartobq-iceberg-geo-testbed/v2_flat_columns/metadata/v1.metadata.json']
);

SELECT COUNT(*) FROM myproj.mydataset.v2_flat_columns
WHERE xmin <= -118 AND xmax >= -125 AND ymin <= 40 AND ymax >= 37;
-- expect 204 rows; total_bytes_processed = 32,000 if pruning to 1/10 files
```

## Refreshing the fixtures

If you re-run `python -m testbed.<name>` (changes seed, encoding, etc.),
re-stage to GCS with:

```bash
python engines/bigquery/_setup.py
```

That rebuilds the gs:// metadata files (sibling `metadata-gcs/` dir
locally) and `gsutil rsync`s data + metadata to the public bucket.
