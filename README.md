# iceberg-geo-testbed

A cross-engine testbed for **Apache Iceberg geospatial support** — V2 and V3 — plus the adjacent GeoParquet path. The goal is one reproducible place to ask, per engine: *can it query geo data through Iceberg today, and does it prune files for spatial predicates?*

> Iceberg V3 (mid-2025) introduced native `geometry`/`geography` types with per-file `lower_bounds`/`upper_bounds` in the manifest. The spec promises that a query like `WHERE ST_Intersects(geom, bbox)` can prune non-overlapping files before touching their data. This repo verifies who actually delivers.

## Conclusions matrix

Last refreshed: **TBD** (run `engines/<engine>/run.py` to update).

| Engine / version | Geo via V2 (flat bbox cols) | Geo via V2 (`bbox` struct) | Geo via V3 (native `geometry`) | Spatial pruning at file level | SRID / CRS handling | Geometry vs Geography | Notes |
|---|---|---|---|---|---|---|---|
| **DuckDB 1.5.3** | ✅ reads, ✅ prunes (1/10 files in probe) | ✅ reads, ❌ doesn't prune (struct-field gap) | ✅ schema parsed, ✅ data scan, ❌ bound deserializer not implemented | Works for top-level numeric columns; broken for geometry column bounds | Reads `geometry(<CRS>)` from metadata.json; CRS visible in column type | Geometry only — geography deserializer also missing | See [docs/duckdb-gap.md](docs/duckdb-gap.md) for the exact source-level gap and PR plan |
| **Snowflake** | ❓ untested | ❓ | ❓ | ❓ | ❓ | ❓ | Runner in `engines/snowflake/` |
| **BigQuery / BigLake** | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | Runner in `engines/bigquery/` |
| **Apache Sedona** | ❓ | ❓ | ❓ (ground-truth implementation) | Expected ✅ — V3 lineage came from their Havasu | ❓ | ❓ | Runner in `engines/sedona/` |
| **PyIceberg 0.11.1** | ✅ read | ✅ read | ⚠️ partial — V3 read landed; no `GeometryType` writer | n/a (catalog only) | n/a | n/a | Tracking [iceberg-python#1818](https://github.com/apache/iceberg-python/issues/1818) |
| **DuckLake 1.0** | — | — | "forthcoming" | — | — | — | Re-test each release |

### Adjacent: GeoParquet (no Iceberg)

Same engines, just `read_parquet(...)` directly. Documented here because it's the alternative path our consumers actually use today.

| Engine | GeoParquet 1.1 covering bbox (per-row-group) | File-level pruning (across many files) | Notes |
|---|---|---|---|
| **DuckDB 1.5.3** | ✅ — prunes row groups within each file | ❌ — opens every file's footer; no manifest equivalent | The motivating problem. ~90s cold for SF-bbox query over the 512-file Overture buildings dataset. |
| **Snowflake** | ❓ | ❓ | |
| **BigQuery** | ❓ | ❓ | |

## What's in here

```
testbed/                   # Engine-agnostic test fixtures
  v2_flat_columns.py       # V2 Iceberg with flat xmin/ymin/xmax/ymax columns + per-file bounds
  v2_bbox_struct.py        # V2 with GeoParquet-1.1-style bbox struct column
  v3_geometry.py           # V3 with native geometry(OGC:CRS84) column
  common.py                # 10-region fixture data + bound-encoding helpers
  _static_catalog.py       # Hand-writes metadata.json + manifest avro + manifest-list

engines/
  duckdb/                  # Working today
  snowflake/               # Planned
  bigquery/                # Planned
  sedona/                  # Planned — reference implementation

docs/
  duckdb-gap.md            # Source-level analysis of the DuckDB 1.5.3 geometry-bound gap
  encoding.md              # V3 geometry bound byte layout per spec
  engine-matrix.md         # Detailed per-engine notes
```

## How the tests work

Each fixture builds a tiny **static Iceberg catalog** — `metadata.json` + manifest avro on disk, no live catalog server — over 10 disjoint world regions × 1000 synthetic rows each. A correct file-level pruner narrows the California-window probe query to **one** file. We grep `Total Files Read:` from `EXPLAIN ANALYZE` (or the engine's equivalent telemetry).

```bash
brew install duckdb              # ≥ 1.5.3
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Build the three fixture tables
python -m testbed.v2_flat_columns
python -m testbed.v2_bbox_struct
python -m testbed.v3_geometry

# Run the DuckDB engine matrix
python engines/duckdb/run.py
```

## Why this exists

In the [`tilerPrototype`](https://github.com/jatorre/tilerPrototype) work the practical wall against GeoParquet for "many files, fast bbox query" was always: DuckDB has to walk every file's footer to evaluate row-group stats — 90+ seconds against an Overture-scale tree on S3. Iceberg V3's per-file geometry bounds in the manifest are the right architectural fix, but engine support is incomplete and inconsistent. This repo isolates the cross-engine verification from the prototype so it can collect collaborators and drive upstream conversations on its own pace.

## Contributing

Open an issue with the engine, version, and minimal repro. PRs welcome for new engine runners, for upstream fixes that land back here as "now passes" rows, or for filling in the `❓` cells in the matrix.

## License

Apache 2.0 — see [LICENSE](./LICENSE).
