# iceberg-geo-testbed

**A cross-engine Apache Iceberg testbed.** We set out to answer one question —
*how good is geospatial support on Iceberg today?* — and uncovered a second,
broader one along the way: *can you even publish a public Iceberg dataset that
any engine can read?* Those are two genuinely different stories, so the repo is
organized as **two tracks**:

| Track | Question | Start here |
|---|---|---|
| 🌍 **Geo on Iceberg** | What's the state of geospatial (V3 `geometry`/`geography`, GeoParquet 2.0) across engines — and what should you actually ship today? | [SPEC.md](./SPEC.md) · [STATUS_V2.md](./STATUS_V2.md) · [STATUS_V3.md](./STATUS_V3.md) · **[BLOG_GEO.md](./BLOG_GEO.md)** |
| 🗂️ **Catalog interop** | Can you publish a *public* Iceberg catalog that any engine consumes? Who can, who can't, and why. | [STATUS_CATALOG.md](./STATUS_CATALOG.md) · [portolan-proxy/](portolan-proxy/) · **[BLOG_CATALOG.md](./BLOG_CATALOG.md)** |

Both tracks share one engine harness (`engines/`) and one set of fixtures
(`testbed/`), and everything is reproducible from public buckets.

---

## 🌍 Track 1 — Geospatial on Iceberg

**Punchline:** Iceberg V3's native geometry + per-file manifest bounds are the
right architecture, but **only Snowflake delivers V3 end-to-end today, and only
for tables it manages itself.** No engine reads a *portable, externally-written*
V3 geometry table yet. The portable answer right now is a V2 convention —
**GeoIceberg V2** ([SPEC.md](./SPEC.md)) — flat `double` bbox columns + a WKB
column + a `geo` table property, which gets file-level spatial pruning on every
engine that reads Iceberg V2. Full story in **[BLOG_GEO.md](./BLOG_GEO.md)**.

### The matrix

Cells show the highest level reached on an L0–L4 ladder (below). Fixture: 10
disjoint regions × 1000 points = 10k rows / 10 files; a California-window probe
should prune to 1 file (196 rows).

| Engine / version | V2 flat-bbox | V2 `bbox` struct | V3 native `geometry` |
|---|---|---|---|
| **DuckDB 1.5.3** | **L3** — prunes to 1/10 files | **L2** — correct, no struct-field pruning | **L2** — type + `ST_AsText(geom)` work (needs GeoParquet-2.0 native typing); spatial predicates now work via [duckdb-iceberg PR #1013](https://github.com/duckdb/duckdb-iceberg/pull/1013) (open) — but full-scan, since the PR defers the geometry-bound deserializer. [#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002) |
| **BigQuery / BigLake** | **L3** | **L3** — prunes through struct fields too | **L0** — `Unknown Iceberg type "geometry(OGC:CRS84)"` |
| **Snowflake** (GA May 2026) | **L3** (`bytes_scanned=0`) | **L3** | **L3 — managed only.** Spatial predicate correct + manifest geometry-bound pruning fires. Unmanaged/external read not yet functional. |
| **Sedona + Iceberg-Spark 1.7.1** | **L3** | **L3** | **L0** — type rejected at parse; can't *write* V3 geometry either (UDT mapper gap) |
| **Databricks (DBSQL 2026.10)** | **L2** *via Snowflake federation* | **L2** *via federation* | **L0** — `GEOMETRY(SRID)`/`GEOGRAPHY(SRID)` work in *Delta*, not in its Iceberg-compat writer; likely coming soon |
| **Oracle ADB 26ai** | **L0** | **L0** | **L0** — can't read our Iceberg tables at all (reader-side; see catalog track) |
| **PyIceberg 0.11.1** | reads | reads | ⚠️ V3 read landed; no `GeometryType` writer ([iceberg-python#1818](https://github.com/apache/iceberg-python/issues/1818)) |

Detail and per-capability breakdowns: **[STATUS_V2.md](./STATUS_V2.md)** (the V2
convention) and **[STATUS_V3.md](./STATUS_V3.md)** (native V3).

**Support ladder:** L0 can't read · L1 full scan · L2 spatial predicate correct
· L3 file-level pruning · L4 row-group pruning (not measured).

### Two facts worth carrying away

- **V3 geometry == GeoParquet 2.0 typing + Iceberg manifest bounds.** A V3
  geometry column's data files must use the *native Parquet Geometry logical
  type* (what GeoParquet 2.0 standardizes), not plain `BINARY` — DuckDB jumped
  L0→L2 the moment we switched. The two specs are coupled.
- **Flat bbox columns prune everywhere; a bbox *struct* doesn't.** DuckDB scans
  all 10 files on a `bbox.xmin` predicate; BigQuery/Sedona prune to 1. For
  portable Iceberg pruning today, use flat `double` columns. (This is why
  GeoIceberg V2 prescribes them.)

---

## 🗂️ Track 2 — Catalog interoperability

**Punchline:** Iceberg's format is open, but the **catalog door is gated.** The
open query engines (DuckDB, Trino, Spark, PyIceberg) consume a public Iceberg
REST catalog with zero credentials; the managed warehouses mostly can't —
their connectors *mandate* auth that a public bucket can't satisfy (the
"empty-credentials trap"). Full story in **[BLOG_CATALOG.md](./BLOG_CATALOG.md)**;
matrix and the storage×catalog×auth analysis in
**[STATUS_CATALOG.md](./STATUS_CATALOG.md)**.

| Engine | Consume the IRC catalog | Read a table by `metadata.json` |
|---|---|---|
| DuckDB / Trino / Spark / PyIceberg | ✅ anonymous (`AUTHORIZATION_TYPE 'none'`) | ✅ |
| Snowflake | ✅ via a permissive CDN / Worker front (+ external volume) | ✅ managed paths |
| BigQuery | ❌ no remote-IRC consumer | ✅ credential-less (`uris=['…metadata.json']`) |
| Databricks | ❌ no generic connector, by design | ❌ UC external location required |
| AWS Glue | ❌ partner-gated federation | n/a |
| Oracle ADB | ❌ needs an OAuth handshake (+ a reader bug) | ❌ reader bug |

### The reference: a serverless, static Iceberg REST catalog ("Portolan")

[`testbed/static_rest_catalog.py`](testbed/static_rest_catalog.py) pre-renders
the IRC read endpoints as plain JSON on a bucket — no server, no DB. Published
public:

```sql
-- DuckDB, zero credentials:
ATTACH 'geo' AS cat (TYPE iceberg,
  ENDPOINT 'https://storage.googleapis.com/cartobq-iceberg-geo-testbed/catalog',
  AUTHORIZATION_TYPE 'none');
SELECT COUNT(*) FROM cat.v2.v2_flat_columns;   -- 10000
```

For the warehouses, a config-only **CDN front** (CloudFront) or the shared,
stateless **Cloudflare Worker** ([portolan-proxy/](portolan-proxy/)) absorbs the
auth header (and the Worker even serves a fake `/v1/oauth/tokens`) — enough to
get Snowflake reading the static catalog end-to-end. See
[STATUS_CATALOG.md](./STATUS_CATALOG.md) for the recipe.

---

## Public, reproducible artifacts

- **Per-table fixtures** (read by pointing any engine at a `metadata.json`):
  ```
  gs://cartobq-iceberg-geo-testbed/{v2_flat_columns,v2_bbox_struct,v2_geo_convention,v3_geometry}/metadata/v1.metadata.json
  ```
- **Static IRC catalog** (namespaces `v2`, `v3`):
  - GCS: `https://storage.googleapis.com/cartobq-iceberg-geo-testbed/catalog`
  - S3: `https://carto-iceberg-geo-testbed-public.s3.us-east-1.amazonaws.com/catalog`
  - via Worker: `https://portolan-irc-proxy.carto-portolan.workers.dev/{gcs|s3}/…`

Each fixture has the same 10,000 rows (10 disjoint regions × 1000 points). The
California-window probe narrows to 1 file / 196 rows for any engine that prunes
manifest bounds.

## Quick start

```bash
git clone https://github.com/jatorre/iceberg-geo-testbed
cd iceberg-geo-testbed
brew install duckdb              # ≥ 1.5.3
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Build local fixtures (deterministic across processes; California probe = 196 rows)
python -m testbed.v2_flat_columns
python -m testbed.v2_bbox_struct
python -m testbed.v2_geo_convention      # the SPEC.md reference implementation
python -m testbed.v3_geometry

# Probe engines
python engines/duckdb/run.py
python engines/bigquery/run.py           # needs `gcloud auth login`

# (Re)build + publish the static catalog
python -m testbed.static_rest_catalog --target=gcs --publish
```

Cloud-engine runners (Snowflake, Databricks, Oracle, Sedona, Polaris) have their
own setup notes under `engines/*/README.md`. Operational context — live cloud
resources, credentials, gotchas — is in **[CLAUDE.md](./CLAUDE.md)**.

## Repo layout

```
SPEC.md            # GeoIceberg V2 — the recommended convention (geo track)
STATUS_V2.md       # Per-engine support for GeoIceberg V2 (geo track)
STATUS_V3.md       # Per-engine support for Iceberg V3 native geometry (geo track)
STATUS_CATALOG.md  # Per-engine catalog-interop matrix (catalog track)
BLOG_GEO.md        # Narrative: geospatial on Iceberg
BLOG_CATALOG.md    # Narrative: publishing open Iceberg / catalog interop
CLAUDE.md          # Operational handoff: cloud resources, creds, gotchas

testbed/           # SHARED — fixtures + the static-catalog generator
  common.py                 # 10-region synthetic data + bound encodings
  _static_catalog.py        # Hand-writes metadata.json + manifest avro (V2 & V3)
  static_rest_catalog.py    # The serverless static IRC catalog generator
  v2_flat_columns.py / v2_bbox_struct.py / v2_geo_convention.py
  v3_geometry.py / v3_geometry_lineage.py

engines/           # SHARED — per-engine runners + READMEs
  duckdb/ bigquery/ snowflake/ sedona/ databricks/ oracle/ polaris/

portolan-proxy/    # Cloudflare Worker: shared IRC proxy (catalog track)
docs/              # duckdb-gap.md (the #1002 analysis) · encoding.md (V3 bounds)
```

## Adjacent: GeoParquet without Iceberg

The motivating problem was ~90s cold for a single-city bbox query over the
512-file Overture buildings dataset on DuckDB — every file's footer opened
because there's no manifest. Iceberg V3's per-file geometry bounds are the
architectural fix; GeoIceberg V2 is the bridge while V3 catches up. GeoParquet
1.1 prunes *row groups within* a file; Iceberg prunes *files* — they compose.

## Contributing

Open an issue with the engine, version, and a minimal repro. PRs welcome for new
engine runners, for upstream fixes that land here as a matrix level-up, or for
filling `❓` cells. For the spec, open questions are at the bottom of
[SPEC.md](./SPEC.md).

## License

Apache 2.0 — see [LICENSE](./LICENSE).
