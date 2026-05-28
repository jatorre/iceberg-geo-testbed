# EMR Serverless probe (V3 geometry)

Submits a small PySpark job to **EMR Serverless 7.13** that tries to
read our public V3 geometry fixture from S3.

## What it tests

Whether AWS-bundled `iceberg-spark-runtime` can map the V3 native
`geometry` type to a Spark type — the same L0/L1 question as the
Dataproc probe, on AWS infrastructure.

## Result (verified 2026-05-28)

**L0** — EMR Serverless 7.13 / Spark 3.5.6-amzn-2 raises
`UnsupportedOperationException: Cannot convert unknown type to Spark:
geometry`. Identical error to Dataproc 2.3 and Sedona + Iceberg-Spark
1.7.1; same upstream Spark-Iceberg gap.

Note: icebergmatrix.org lists "EMR (8.0 Spark): Full" for V3 Geometry,
but `emr-8.x` doesn't exist yet in `aws emr list-release-labels` —
latest is `emr-7.13.0`. That column appears to be forward-looking;
the actually-testable EMR is L0.

## How to re-run

### One-time setup (creates the IAM role + EMR Serverless app)

```bash
# IAM role for the EMR Serverless job
aws iam create-role --role-name emr-iceberg-probe-role \
  --assume-role-policy-document file://trust.json
aws iam put-role-policy --role-name emr-iceberg-probe-role \
  --policy-name s3-access --policy-document file://policy.json

# EMR Serverless application (no cost when idle, initial-capacity 0)
aws emr-serverless create-application \
  --name iceberg-geo-probe \
  --release-label emr-7.13.0 \
  --type SPARK \
  --region us-east-1
```

(`trust.json` / `policy.json` are tiny — see the AWS docs or the
inline snippets we used in `engines/snowflake/_provision.py` for the
shape. The S3 read of `carto-iceberg-geo-testbed-public/*` is enough.)

### Submit + read logs

```bash
aws s3 cp probe.py s3://carto-iceberg-geo-testbed-public/probes/emr_v3_probe.py

aws emr-serverless start-job-run \
  --application-id <APP_ID> \
  --execution-role-arn arn:aws:iam::<acct>:role/emr-iceberg-probe-role \
  --name iceberg-v3-geo-probe \
  --region us-east-1 \
  --job-driver '{"sparkSubmit": {"entryPoint": "s3://carto-iceberg-geo-testbed-public/probes/emr_v3_probe.py", "sparkSubmitParameters": "--conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions --conf spark.sql.catalog.default_iceberg=org.apache.iceberg.spark.SparkCatalog"}}' \
  --configuration-overrides '{"monitoringConfiguration": {"s3MonitoringConfiguration": {"logUri": "s3://carto-iceberg-geo-testbed-public/emr-logs/"}}}'

# poll the job; when SUCCESS or FAILED, fetch:
#   s3://carto-iceberg-geo-testbed-public/emr-logs/applications/<APP_ID>/jobs/<JOB_ID>/SPARK_DRIVER/stdout.gz
```

The probe reads from
`s3://carto-iceberg-geo-testbed-public/v3_geometry/metadata/v1.metadata.json`
(public bucket, no credentials required for the data itself; the
execution role just needs S3 read access for the entry script + log
writes).
