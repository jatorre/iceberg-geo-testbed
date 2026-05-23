# BigQuery / BigLake engine runner — TODO

Google announced V3 Iceberg support via BigLake with native geospatial column
stats. Mirror of the Snowflake plan: stage the parquet to GCS, register as a
BigLake-managed Iceberg table, run the probe query, check the per-file
pruning telemetry in the query stats.

Open questions:
- Whether BigLake currently honors V3 geometry bounds for predicate pushdown
  on `ST_INTERSECTS`, or only column bounds.
- How to read the equivalent of "files scanned" from the BQ job stats.
