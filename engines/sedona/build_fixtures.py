"""Build the same Iceberg fixtures as testbed.* using Apache Sedona's
native V3 geometry writer.

Run this *inside* the Sedona Docker image — it has Spark 3.4 + Sedona +
JTS on the classpath. The Iceberg connector is pulled via --packages at
spark-submit time (see run.sh).

Expected outputs under /workspace/engines/sedona/work/warehouse/<table>/:
  - data/*.parquet
  - metadata/*.metadata.json
  - metadata/*manifest*.avro

The manifest avro produced by Iceberg's own writer is the GROUND TRUTH we
diff against testbed/_static_catalog.py's hand-written manifest avro.

We pre-generate the synthetic row data in Python (using the same stable
seed as `testbed.common.stable_seed`) so the data values match the
testbed-built fixtures bit-for-bit. Spark just packages it into the
Iceberg layout.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from testbed.common import REGIONS, stable_seed  # noqa: E402

from pyspark.sql import SparkSession  # noqa: E402
from sedona.spark import SedonaContext  # noqa: E402


CATALOG = "iceberg"
WAREHOUSE = REPO / "engines" / "sedona" / "work" / "warehouse"


def build_spark() -> SparkSession:
    builder = (
        SparkSession.builder.appName("sedona-iceberg-testbed")
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions,"
            "org.apache.sedona.viz.sql.SedonaVizExtensions,"
            "org.apache.sedona.sql.SedonaSqlExtensions",
        )
        .config(f"spark.sql.catalog.{CATALOG}", "org.apache.iceberg.spark.SparkCatalog")
        .config(f"spark.sql.catalog.{CATALOG}.type", "hadoop")
        .config(f"spark.sql.catalog.{CATALOG}.warehouse", str(WAREHOUSE))
        .config("spark.sql.session.timeZone", "UTC")
    )
    spark = builder.getOrCreate()
    return SedonaContext.create(spark)


def gen_rows(region) -> list[dict]:
    """Generate 1000 rows for one region, deterministic across processes
    via stable_seed. Each row has id + (xmin, ymin, xmax, ymax)."""
    rng = random.Random(stable_seed(region.name))
    rows = []
    for i in range(1000):
        x0 = rng.uniform(region.xmin, region.xmax)
        y0 = rng.uniform(region.ymin, region.ymax)
        rows.append(
            {
                "id": f"{region.name}-{i}",
                "xmin": x0,
                "ymin": y0,
                "xmax": x0 + 0.001,
                "ymax": y0 + 0.001,
                "x": x0,   # for v3_geometry: point coord
                "y": y0,
            }
        )
    return rows


def build_v2_flat(spark) -> None:
    table = f"{CATALOG}.v2_flat_columns"
    spark.sql(f"DROP TABLE IF EXISTS {table}")
    spark.sql(
        f"""
        CREATE TABLE {table} (
          id STRING,
          xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE
        ) USING iceberg
        TBLPROPERTIES ('format-version'='2')
        """
    )
    for region in REGIONS:
        rows = [
            {"id": r["id"], "xmin": r["xmin"], "ymin": r["ymin"], "xmax": r["xmax"], "ymax": r["ymax"]}
            for r in gen_rows(region)
        ]
        df = spark.createDataFrame(rows).repartition(1)
        df.writeTo(table).append()
        print(f"  appended {region.name}")


def build_v3_geometry(spark) -> None:
    table = f"{CATALOG}.v3_geometry"
    spark.sql(f"DROP TABLE IF EXISTS {table}")

    # Approach A: let Spark/Sedona infer the schema from a DataFrame that
    # already has a Geometry-typed column. If iceberg-spark-runtime knows
    # how to map GeometryType → Iceberg V3 GeometryType, we get a native V3
    # column. Otherwise it'll either reject the write or fall back to binary.
    sample = [{"id": r["id"], "x": r["x"], "y": r["y"]} for r in gen_rows(REGIONS[0])]
    df = (
        spark.createDataFrame(sample)
        .selectExpr("id", "ST_Point(x, y) as geom")
        .repartition(1)
    )
    print(f"  inferred geom column dtype: {df.schema['geom'].dataType.simpleString()}")

    column_kind = "GEOMETRY"
    try:
        # writeTo + .using('iceberg') + .tableProperty('format-version', '3')
        # is the DataFrame API equivalent of CREATE TABLE WITH ... USING iceberg
        (
            df.writeTo(table)
            .using("iceberg")
            .tableProperty("format-version", "3")
            .create()
        )
    except Exception as e:
        msg = str(e) or repr(e)
        first = msg.splitlines()[0] if msg.splitlines() else "(empty)"
        print(f"  GEOMETRY DataFrame CTAS rejected. type={type(e).__name__} first_line={first!r}")
        print(f"  full message:\n{msg[:2000]}")
        column_kind = "BINARY"
        spark.sql(
            f"""
            CREATE TABLE {table} (
              id STRING,
              geom BINARY
            ) USING iceberg
            TBLPROPERTIES ('format-version'='3')
            """
        )
        # Then append the first region's data we'd already created
        df_bin = df.selectExpr("id", "ST_AsBinary(geom) as geom")
        df_bin.writeTo(table).append()
    print(f"  column kind: {column_kind}")
    print(f"  appended {REGIONS[0].name}")

    # Inspect what was created.
    spark.sql(f"DESCRIBE TABLE {table}").show(truncate=False)
    spark.sql(f"SELECT * FROM {table}.metadata_log_entries LIMIT 1").show(truncate=False)

    # Remaining 9 regions
    for region in REGIONS[1:]:
        rows = [{"id": r["id"], "x": r["x"], "y": r["y"]} for r in gen_rows(region)]
        df = spark.createDataFrame(rows)
        if column_kind == "GEOMETRY":
            df = df.selectExpr("id", "ST_Point(x, y) as geom")
        else:
            df = df.selectExpr("id", "ST_AsBinary(ST_Point(x, y)) as geom")
        df = df.repartition(1)
        df.writeTo(table).append()
        print(f"  appended {region.name}")


def main() -> int:
    print("starting Spark + Sedona…")
    spark = build_spark()
    print(f"spark version: {spark.version}")
    spark.sparkContext.setLogLevel("WARN")

    print("\n=== v2_flat_columns ===")
    build_v2_flat(spark)

    print("\n=== v3_geometry ===")
    build_v3_geometry(spark)

    spark.stop()
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
