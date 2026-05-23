# Snowflake engine runner — TODO

Snowflake announced V3 support with native `GEOMETRY` / `GEOGRAPHY` types and
per-file manifest bbox pruning. This runner is a placeholder for verifying
that end-to-end.

Plan:

1. Stage the same 10 regional parquet files into a Snowflake external stage
   (`@my_stage/v3_geometry/data/`).
2. Create an Iceberg table from the existing static metadata via
   `CREATE ICEBERG TABLE ... CATALOG = 'OBJECT_STORE_CATALOG' METADATA_FILE_PATH = '...'`.
3. Run the same `ST_Intersects` probe query.
4. Inspect the query profile (`SYSTEM$EXPLAIN_PLAN_JSON(...)`) for the
   `partitionsScanned` / `bytesScanned` numbers; assert that only one file's
   worth of data was touched.

Open questions:
- Does Snowflake's "OBJECT_STORE_CATALOG" mode read our hand-written
  static metadata.json, or does it require an Iceberg REST catalog?
- Which Snowflake account types/regions have V3 GA today vs preview?

Contributions welcome.
