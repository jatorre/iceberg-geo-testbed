"""EMR Serverless V3-geometry probe (same shape as engines/dataproc/probe.py
but reads from S3 instead of GCS — AWS doesn't bundle the GCS Hadoop FS
connector). Submit via `aws emr-serverless start-job-run` — see README.md.
"""
import sys
import traceback

from pyspark.sql import SparkSession

METADATA = "s3://carto-iceberg-geo-testbed-public/v3_geometry/metadata/v1.metadata.json"


def main() -> int:
    spark = (
        SparkSession.builder
        .appName("emr-v3-geo-probe")
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config(
            "spark.sql.catalog.default_iceberg",
            "org.apache.iceberg.spark.SparkCatalog",
        )
        .getOrCreate()
    )

    print(f"\nspark.version: {spark.version}", flush=True)
    try:
        spark._jvm.Class.forName("org.apache.iceberg.spark.SparkCatalog")
        print("iceberg jar present: True", flush=True)
    except Exception as e:
        print(f"ICEBERG NOT ON CLASSPATH: {e}", flush=True)
        spark.stop()
        return 1

    print(f"\n=== reading V3 geometry table at {METADATA} ===", flush=True)
    try:
        df = spark.read.format("iceberg").load(METADATA)
        df.printSchema()
        n = df.count()
        print(f"L1 OK — full scan = {n} rows", flush=True)
        if n != 10000:
            print(f"!! expected 10000, got {n}", flush=True)
    except Exception:
        print("L0 — read failed:", flush=True)
        traceback.print_exc()
        spark.stop()
        return 0

    spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
