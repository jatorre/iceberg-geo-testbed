# DuckDB engine runner

Drives `duckdb` (>= 1.5.6) against each baseline table via `iceberg_scan(...)`.
Parses `Total Files Read:` from `EXPLAIN ANALYZE` and compares against the
expected pruning behavior.

```bash
# From repo root:
python -m testbed.v2_flat_columns
python -m testbed.v2_bbox_struct
python -m testbed.v3_geometry
python -m testbed.v3_geography
python engines/duckdb/run.py
```

Expected output (2026-10-09, DuckDB 1.5.6, iceberg extension `890b78a9c`, spatial `04270fe`):

```
case                       expected     actual  notes
--------------------------------------------------------------------------------
  v2_flat_columns                 1          1  manifest pruning works for top-level numeric columns
  v2_bbox_struct                 10         10  struct-field predicates don't push to manifest bounds
  v3_geometry/intersects         10         10  N3: correct rows, but no bbox pre-filter is derived -> full scan
  v3_geometry/extent              1          1  N4: manifest geometry-bound pruning (packed_xy_le decoded)
  v3_geography/extent        errors        ERR  Iceberg-layer gap: `Not implemented Error: Geography support`
```

## What changed since 1.5.3

On 1.5.3 the `v3_geometry` probe crashed in `IcebergValue::DeserializeValue`
(no `GEOMETRY` branch) — see [docs/duckdb-gap.md](../../docs/duckdb-gap.md).
[duckdb-iceberg#1030](https://github.com/duckdb/duckdb-iceberg/pull/1030)
(merged 2026-06-10, in the stock extension) decodes the V3 `packed_xy_le`
bounds into a `GeometryStats` extent and prunes files through
`GeometryStats::CheckZonemap`. That zonemap check only understands the
bbox-only predicates `&&` and `ST_Intersects_Extent`; a plain `ST_Intersects`
is *not* rewritten into a bbox pre-filter (there is no such optimizer rule in
duckdb-spatial 1.5.6), so it evaluates correctly but reads every file.

The portable pattern for exact semantics **and** pruning:

```sql
WHERE ST_Intersects_Extent(geom, env) AND ST_Intersects(geom, env)
--    ^ prunes files via manifest bounds   ^ exact geometry test
```

Verified on the same DuckDB against Snowflake's own managed V3 table
(`gs://cartobq-iceberg-geo-testbed-eu/managed-v3-geo.MLyhYkeQ/`): 2/7 files
read with the extent predicate, 7/7 with plain `ST_Intersects`.

## Still open

- `geography` is rejected by `iceberg_scan` (`Not implemented Error:
  Geography support`) while `read_parquet` on the same files works — the gap
  is in duckdb-iceberg's type mapping, not in the Parquet reader.
- A `ST_Intersects` -> bbox pre-filter rewrite would make pruning automatic;
  that belongs in duckdb-spatial.
