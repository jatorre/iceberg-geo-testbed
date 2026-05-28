# Dataproc Serverless probe (V3 geometry)

Submits a small PySpark batch job to **Dataproc Serverless 2.3** that
tries to read our public V3 geometry fixture from GCS.

## What it tests

Whether the bundled `iceberg-spark-runtime` can map the V3 native
`geometry` type to a Spark type. If `spark.read.format("iceberg").load(...)`
returns a DataFrame, the engine is at least L1; if it raises
`UnsupportedOperationException: Cannot convert unknown type to Spark:
geometry`, it's L0.

(There's no spatial library on the default Dataproc Serverless image,
so we don't go past L1 here — the L0/L1 boundary is the question that
matters today.)

## Result (verified 2026-05-28)

**L0** — Dataproc Serverless 2.3 / Spark 3.5.3 raises
`UnsupportedOperationException: Cannot convert unknown type to Spark:
geometry`. Same upstream gap as Sedona + Iceberg-Spark 1.7.1.

## How to re-run

```bash
gcloud dataproc batches submit pyspark probe.py \
  --batch="v3-geo-probe-$(date +%s)" \
  --region=us-central1 \
  --version=2.3 \
  --project=cartobq \
  --deps-bucket=gs://cartobq-iceberg-geo-testbed \
  --properties="spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions,spark.sql.catalog.default_iceberg=org.apache.iceberg.spark.SparkCatalog"
```

The probe reads from
`gs://cartobq-iceberg-geo-testbed/v3_geometry/metadata/v1.metadata.json`
(public, no credentials needed beyond the Dataproc service account).
