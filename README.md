# iceberg-geo-testbed

A small testbed for **Apache Iceberg V3 geospatial support across query engines**. The goal is to characterize, end-to-end, where each engine actually delivers on the V3 spec promise of *per-file spatial pruning via geometry bounds in the manifest* — and to feed back any gaps as upstream PRs (DuckDB, PyIceberg, etc.).

> Iceberg V3 (mid-2025) added native `geometry` and `geography` types with per-file `lower_bounds` / `upper_bounds` in the manifest. In principle, a query like `WHERE ST_Intersects(geom, bbox_literal)` should skip non-overlapping files before opening any footer. That's the test we're running here, engine by engine.

## Status (2026-05-23)

| Engine | V3 geo schema | Data scan | **Manifest-level geometry pruning** | Notes |
|---|---|---|---|---|
| **DuckDB 1.5.3** | ✅ reads `geometry(OGC:CRS84)` from `metadata.json` | ✅ | ❌ **`DeserializeValue` lacks a GEOMETRY branch** → `Failed to deserialize blob … attempting to produce value of type 'GEOMETRY(...)'`. Pruning bails. | First-shipped (May 20 2026). Half-wired. PR opportunity: add the geometry branch to [`IcebergValue::DeserializeValue`](https://github.com/duckdb/duckdb-iceberg/blob/main/src/core/expression/iceberg_value.cpp). |
| **DuckDB 1.5.3 (V2 fallback, flat bbox cols)** | n/a — `xmin/ymin/xmax/ymax` doubles | ✅ | ✅ Prunes 9 of 10 files in our synthetic test | The working answer today if you control the schema. |
| **DuckDB 1.5.3 (V2, `bbox` struct cols)** | n/a — `bbox.{xmin,…}` doubles | ✅ | ❌ Doesn't push struct-field predicates to manifest | GeoParquet 1.1 covering convention loses to this. |
| **Snowflake** | _untested here_ | — | Per Snowflake docs, manifest bbox stats are used for pruning. Need to verify with a hosted table. | |
| **Wherobots / Sedona** | _untested here_ | — | Production user of V3 geo manifest pruning (their Havasu predecessor → upstream V3). | |
| **PyIceberg** | ❌ no `GeometryType` writer (0.11.1) | n/a | n/a | Tracking issue: [iceberg-python#1818](https://github.com/apache/iceberg-python/issues/1818). |
| **DuckLake** | "forthcoming" geometry stats per 0.3 notes | — | — | Re-test on every release. |

## What's in here

```
testbed/                   # Self-contained test runners
  v2_flat_columns.py       # V2 Iceberg with flat xmin/ymin/xmax/ymax columns + per-file bounds
  v2_bbox_struct.py        # V2 with bbox struct; shows struct-pushdown gap
  v3_geometry.py           # V3 with native geometry column; expected DuckDB failure
  common.py                # Shared region fixtures + bound encoding helpers

engines/
  duckdb/                  # Runner + EXPLAIN ANALYZE comparison scripts
  snowflake/               # (TODO) Stage tables into Snowflake, run same queries via /sql, log pruning telemetry
  bigquery/                # (TODO) BigLake-hosted V3 tables, same comparison
  sedona/                  # (TODO) Docker-based runner, ground-truth implementation of V3 geo

docs/
  encoding.md              # Iceberg V3 geometry bound byte format (per spec)
  duckdb-gap.md            # Exact source-level gap, repro, proposed PR
  engine-matrix.md         # Live matrix of who supports what
```

## Run the DuckDB baseline tests

```bash
brew install duckdb              # ≥1.5.3
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python testbed/v2_flat_columns.py    # → builds table, prints query + EXPLAIN
python testbed/v2_bbox_struct.py
python testbed/v3_geometry.py        # → expected: DuckDB deserialize error today
```

Each runner builds a **static Iceberg catalog** by hand (no live REST catalog — just `metadata.json` + manifest avro files on disk), then queries via DuckDB's `iceberg_scan(...)`. The schema is 10 disjoint world regions × 1000 fake points each, so file-level pruning should narrow to one file for any tight bbox query. We grep `Total Files Read:` from `EXPLAIN ANALYZE` to assert the pruning behavior.

## Why this exists

In the [`tilerPrototype`](https://github.com/jatorre/tilerPrototype) work I ran into the practical limits of GeoParquet for "many files, fast bbox query" workloads — DuckDB has to walk every file footer to evaluate row-group stats, which is 90+ seconds against an Overture-scale tree on S3. Iceberg V3's per-file geometry bounds in the manifest are the right architectural fix, but DuckDB 1.5.3 (May 20 2026) shipped V3 geo read support with the bound deserializer still incomplete. This repo isolates that experiment from the prototype so it can move at its own pace, attract collaborators, and accumulate cross-engine evidence for upstream conversations.

## Contributing

Open an issue with the engine, version, and minimal repro. PRs welcome for new engine runners or for upstream fixes that land back here as "now passes" rows.

## License

Apache 2.0 — see [LICENSE](./LICENSE).
