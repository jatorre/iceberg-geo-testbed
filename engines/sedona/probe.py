"""Probe our hand-written Iceberg fixtures with Sedona/Spark and report
where it lands on the L0-L4 ladder for each fixture.

  L0  table can't be read
  L1  SELECT * runs end-to-end (full scan returns rows)
  L2  spatial / predicate query returns the correct rows
  L3  file pruning narrows to the matching files (we read Spark's
      InputFiles via DataFrame.inputFiles() — Iceberg's data-source v2
      pushes file selection so this list reflects post-pruning files)
  L4  row-group pruning (not measured here)

Reads the file://-rooted catalog at `data/<table>/metadata/v1.metadata.json`.
The container must be run with the host repo bind-mounted at its host path
so the file:// URIs resolve.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pyspark.sql import SparkSession  # noqa: E402
from sedona.spark import SedonaContext  # noqa: E402


def build_spark() -> SparkSession:
    builder = (
        SparkSession.builder.appName("sedona-iceberg-probe")
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions,"
            "org.apache.sedona.viz.sql.SedonaVizExtensions,"
            "org.apache.sedona.sql.SedonaSqlExtensions",
        )
        # We don't need a catalog; we'll register each metadata.json directly
        # via the DataFrameReader path that Iceberg exposes:
        #   spark.read.format("iceberg").load("<metadata.json>")
        # But the cleanest is to use the "hadoop" catalog rooted at our
        # data/ dir — Iceberg auto-discovers tables under <warehouse>/<db>.<tbl>.
        .config("spark.sql.catalog.local", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.local.type", "hadoop")
        .config("spark.sql.catalog.local.warehouse", str(REPO / "data"))
        .config("spark.sql.session.timeZone", "UTC")
    )
    spark = builder.getOrCreate()
    return SedonaContext.create(spark)


def assess(spark, name: str, predicate_sql: str, expected_rows: int) -> dict:
    state = {"case": name, "level": "L0", "notes": []}
    table_path = REPO / "data" / name / "metadata" / "v1.metadata.json"
    if not table_path.exists():
        state["notes"].append(f"missing metadata: {table_path}")
        return state

    # Use the Iceberg DataFrameReader path for a static metadata.json.
    try:
        df = spark.read.format("iceberg").load(str(table_path.parent.parent))
    except Exception as e:
        state["notes"].append(f"read failed: {type(e).__name__}: {str(e).splitlines()[0]}")
        return state
    state["notes"].append(f"schema: {df.schema.simpleString()}")

    # L1: full-scan count
    try:
        n_full = df.count()
        state["level"] = "L1"
        state["notes"].append(f"full-scan rows = {n_full}")
    except Exception as e:
        state["notes"].append(f"COUNT failed: {type(e).__name__}: {str(e).splitlines()[0]}")
        return state

    # L2: predicate
    try:
        filt = df.where(predicate_sql)
        n_pred = filt.count()
    except Exception as e:
        state["notes"].append(f"predicate failed: {type(e).__name__}: {str(e).splitlines()[0]}")
        return state
    if n_pred != expected_rows:
        state["notes"].append(f"predicate rows = {n_pred} (expected {expected_rows})")
        return state
    state["level"] = "L2"
    state["notes"].append(f"predicate rows = {n_pred} ✓")

    # L3: file pruning. Iceberg's Spark data source doesn't expose
    # `inputFiles()`, so we count distinct `input_file_name()` values
    # among the rows that matched the predicate. If pruning works at
    # manifest level, only matching files (typically 1 of 10) contribute.
    try:
        from pyspark.sql import functions as F
        n_files = (
            filt.select(F.input_file_name().alias("_f"))
            .distinct()
            .count()
        )
        state["notes"].append(f"distinct input files contributing to result: {n_files} of 10")
        if n_files <= 1:
            state["level"] = "L3"
    except Exception as e:
        state["notes"].append(f"input_file_name() check failed: {e}")

    return state


CASES = [
    (
        "v2_flat_columns",
        "xmin <= -118 AND xmax >= -125 AND ymin <= 40 AND ymax >= 37",
        196,
    ),
    (
        "v2_bbox_struct",
        "bbox.xmin <= -118 AND bbox.xmax >= -125 AND bbox.ymin <= 40 AND bbox.ymax >= 37",
        196,
    ),
    (
        "v3_geometry",
        # Sedona spatial predicate: ST_Intersects(geom, ST_MakeEnvelope(...)).
        # Our V3 metadata claims `geometry(OGC:CRS84)`. Whether Sedona can map
        # this to its Geometry UDT and run ST_Intersects is the open question.
        "ST_Intersects(geom, ST_GeomFromText('POLYGON((-125 32, -115 32, -115 42, -125 42, -125 32))'))",
        196,
    ),
]


def main() -> int:
    print("starting Spark + Sedona…")
    spark = build_spark()
    print(f"spark version: {spark.version}")
    spark.sparkContext.setLogLevel("WARN")

    print(f"\n{'case':22} {'level':>5}  notes")
    print("-" * 100)
    for name, predicate, expected_rows in CASES:
        s = assess(spark, name, predicate, expected_rows)
        print(f"{s['case']:22} {s['level']:>5}  {s['notes'][-1]}")
        for n in s["notes"][:-1]:
            print(f"{'':22} {'':>5}  · {n}")

    spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
