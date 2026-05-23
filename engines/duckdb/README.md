# DuckDB engine runner

Drives `duckdb` (≥ 1.5.3) against each baseline table via `iceberg_scan(...)`.
Parses `Total Files Read:` from `EXPLAIN ANALYZE` and compares against the
expected pruning behavior.

```bash
# From repo root:
python -m testbed.v2_flat_columns
python -m testbed.v2_bbox_struct
python -m testbed.v3_geometry
python engines/duckdb/run.py
```

Expected output (today, May 2026, DuckDB 1.5.3):

```
case                       expected     actual  notes
--------------------------------------------------------------------------------
  v2_flat_columns                 1          1  manifest pruning works for top-level numeric columns
  v2_bbox_struct                 10         10  struct-field predicates don't push to manifest bounds
  v3_geometry                errors        ERR  GEOMETRY bound deserialization not implemented in 1.5.3
```

When DuckDB ships the GEOMETRY case for `IcebergValue::DeserializeValue`, the
v3_geometry row's expected value will flip to `1`.
