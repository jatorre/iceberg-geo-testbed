# Geospatial on Apache Iceberg in 2026: where V3 geometry actually stands, and a V2 bridge that works today

*2026-05-28*

Apache Iceberg V3 was announced in mid-2025 with native `geometry` and
`geography` types and **per-file geometry bounds in the manifest**. On paper
that's the right architecture for the long-standing "many parquet files, fast
bbox query" problem in geospatial: instead of every engine opening every file's
footer to decide what to read, the Iceberg manifest tells you *up front* which
files can be skipped. It's the same leap GeoParquet made one layer down — and
it's the fix for the classic "~90 seconds to query a single city out of a
512-file Overture dataset" pain.

We built [`iceberg-geo-testbed`](https://github.com/jatorre/iceberg-geo-testbed)
to find out how real that promise is today: one repo, the same hand-written
V2/V3 fixtures, probed across DuckDB, BigQuery/BigLake, Snowflake,
Sedona/Iceberg-Spark, Databricks, and Oracle ADB, aggregated into a single
matrix. Everything is reproducible from a public bucket.

## The headline

**Snowflake delivers V3 geometry end-to-end — for both managed *and*
externally-written tables — provided the external writer matches Snowflake's
exact V3 shape.** We initially thought this was "managed only"; that was a
writer-shape issue on our side, not a Snowflake limitation. The other engines
sit between "rejects the type at parse" (BigQuery, Sedona, Databricks, Oracle)
and "reads the column, doesn't prune on geometry bounds" (DuckDB with the
in-flight crash-fix PR).

So if you have geospatial data and you're in the Snowflake ecosystem, V3 is
ready. If you need *broad cross-engine portability* today, V3 still isn't —
only Snowflake's V3 reader is. The good news for that case: there's a V2
convention that gets you file-level spatial pruning across every engine that
reads Iceberg V2 — modelled directly on how GeoParquet 1.1 solved the same
problem at the Parquet layer.

## What we measured

Synthetic fixture: 10 geographically disjoint regions × 1000 random points =
10,000 rows across 10 parquet files in one Iceberg table. A "California window"
probe should — with correct file pruning — read 1 of 10 files (196 matching
rows). We graded the highest level each engine reached:

- **L0** — table can't be read
- **L1** — full scan works
- **L2** — spatial predicate returns correct rows
- **L3** — file-level pruning works (the manifest narrows the scan)
- **L4** — row-group pruning inside surviving files (not measured here)

Across three table shapes — **V2 flat bbox columns**, **V2 bbox struct**
(GeoParquet-1.1 style), and **V3 native geometry**:

| Engine | V2 flat | V2 struct | V3 native geometry |
|---|---|---|---|
| **DuckDB 1.5.3** | **L3** | L2 (no struct pruning) | **L2** — type + `ST_AsText(geom)` work; spatial predicates land via the in-flight [PR #1013](https://github.com/duckdb/duckdb-iceberg/pull/1013), but it skips the geometry-bound decoder, so it's a full scan ([#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002)) |
| **BigQuery / BigLake** | **L3** | **L3** | **L0** — `Unknown Iceberg type "geometry(OGC:CRS84)"` |
| **Snowflake** (GA May 2026) | **L3** | **L3** | **L3 — managed *and* externally-written.** Spatial predicate correct *and* manifest geometry-bound pruning fires on both. The external path requires matching Snowflake's exact V3 shape (lineage cols, snapshot fields, etc. — see below). |
| **Sedona + Iceberg-Spark 1.7.1** | **L3** | **L3** | **L0** — type rejected at parse; also can't *write* V3 geometry (UDT mapper missing) |
| **Databricks (DBSQL 2026.10)** | (via federation) | (via federation) | **L0** — `GEOMETRY(SRID)`/`GEOGRAPHY(SRID)` work in *Delta*, but the Iceberg-compat writer rejects them; geo-in-Iceberg likely coming soon |
| **Oracle ADB 26ai** | **L0** | **L0** | **L0** — can't read our Iceberg tables at all (reader-side, not a geometry-specific issue) |

(How Databricks and Oracle *reach* a table — the catalog/auth plumbing — is its
own story, told in the [catalog-interop write-up](./BLOG_CATALOG.md). Here we
care about the geometry side.)

## The findings worth knowing

**Snowflake's V3 geometry actually works — and the architecture proves out for
externally-written tables too.** A Snowflake-managed `GEOMETRY` table (note:
`ICEBERG_VERSION=3` is required — the default for new Iceberg tables is V2,
and the error if you forget doesn't hint at it) returns correct
spatial-predicate results *and* prunes on the manifest's geometry bounds. We
initially thought this only held for tables Snowflake itself manages; in fact,
once we inspected one of Snowflake's own managed V3 parquet files we
discovered the exact writer-shape its strict V3 reader expects, and pointed
Snowflake at an externally-written fixture matching that shape — it works
end-to-end (CREATE → 10000 rows → spatial predicate → file pruning at L3,
~25 KB scanned vs ~256 KB for a full GEOM scan). The non-obvious requirements:
the parquet must carry Snowflake-internal `METADATA$RL_ROW_ID` (field id
2147483540) and `METADATA$RL_LAST_UPDATED_SEQUENCE_NUMBER` (field id
2147483539) int64 columns filled with NULL, the metadata.json must omit the
`row-lineage` key entirely (not `false`), set `last-column-id: 4` (reserving
slots for the lineage cols), and the snapshot block needs the V3 fields
(`first-row-id`, `added-rows`) plus a full append summary. With those, the
headline V3 feature isn't vaporware — it works across the catalog boundary.
The repo's `testbed/v3_geometry_snowflake_lineage.py` is the reference writer.

**V3 geometry needs GeoParquet-2.0-style native typing in the data files —
plain WKB-in-`BINARY` isn't enough.** This is the single most useful practical
finding for anyone hand-writing V3. DuckDB jumped from L0 to L2 the moment we
wrote the geometry column with a native Parquet `Geometry` logical type (via
`geoarrow-pyarrow`) instead of a plain `BINARY` column. Iceberg V3 geometry maps
to the **native Parquet Geometry logical type that GeoParquet 2.0 standardizes**
— an engine won't recognize a `BINARY` column as geometry no matter what the
Iceberg schema's type token says. The two specs are coupled: Iceberg V3 geometry
*is* GeoParquet 2.0 typing at the file level, wrapped in Iceberg's manifest
bounds at the table level.

**DuckDB is L2 — not the "full" the public matrices claim, but not broken
either.** [icebergmatrix.org](https://icebergmatrix.org) lists DuckDB V3 geometry
as `full`. Hands-on: the type parses, and `SELECT geom` / `ST_AsText(geom)`
return clean geometries — but on stock 1.5.3 a spatial *predicate* trips a
manifest bound deserializer (`IcebergValue::DeserializeValue`) that has no
`GEOMETRY` branch and crashes. We filed
[duckdb-iceberg#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002), and
the maintainer responded with [PR #1013](https://github.com/duckdb/duckdb-iceberg/pull/1013).
We built it and re-tested: the crash is gone and the spatial predicate returns
the right rows. The PR explicitly *skips* decoding the geometry bound (it
returns empty stats), so DuckDB falls back to a full scan instead of pruning —
making the predicate work without yet delivering L3. EXPLAIN ANALYZE confirms
10/10 files on our fixture and 7/7 on Snowflake's managed V3. So the L2 cell is
now solid (no crash, correct results) on both fixtures; closing the remaining
gap to L3 needs a follow-up that actually decodes the V3 `packed_xy_le` bound.

**The V2 bbox *struct* is not a portable pruning strategy; flat columns are.**
A GeoParquet-1.1-style covering `bbox STRUCT<xmin,ymin,...>` reads fine
everywhere, but pruning on its leaf fields is engine-dependent: DuckDB scans all
10 files on a `bbox.xmin` predicate; BigQuery and Sedona prune to 1. **Flat
top-level `double` bbox columns prune on every engine we tested.** That's a
concrete schema-design recommendation: if you want portable Iceberg file
pruning today, put the bbox in flat columns, not a struct.

**Our reference metadata is spec-compliant by the reference catalog.** Apache
Polaris (the reference open-source Iceberg REST catalog) accepts all our V2
fixtures, and our V3 too — after we patched a real pyiceberg gap it caught
(it omits the V3-required `next-row-id`/`row-lineage` fields). So when an engine
rejects our tables, it's the engine being stricter than the spec, not us.

## The historical parallel: this exact movie already played at the Parquet layer

Parquet didn't natively understand "geometry" for years. The community defined
**GeoParquet 1.1** as a *convention on top of Parquet*: a covering `bbox` struct
(per row group) + a WKB `geometry` column + a `geo` metadata block declaring CRS,
encoding, and which column is which. Engines that understood the convention
pruned row groups against the bbox struct's column statistics; engines that
didn't still read the file as plain Parquet. When Parquet later gained native
geometry types, **GeoParquet 2.0** was the migration target — the convention had
defined the data model clearly enough that the spec just absorbed it.

**Iceberg is at exactly the point Parquet was three years ago.** V3 geometry is
the eventual target; the ecosystem isn't there yet. So we propose the same
pattern, one layer up.

## The bridge: GeoIceberg V2

The full normative document is [SPEC.md](./SPEC.md). The shape: for each
geometry, a GeoIceberg V2 table carries

- a `geom_wkb BINARY` column (the WKB payload),
- four `DOUBLE` columns (`xmin/ymin/xmax/ymax`) — the per-row bounding rectangle,
  which is what Iceberg's manifest prunes on, and
- a `geo` table property (JSON, same shape as GeoParquet 1.1's `geo` block)
  declaring CRS, edges, encoding, and which columns are bbox vs payload.

Standard Iceberg V2 writers automatically record per-file manifest bounds on
`double` columns, so **file pruning comes for free** once the bbox columns
exist. The query pattern is exactly what GeoParquet 1.1 users already write:

```sql
SELECT id, ST_GeomFromWKB(geom_wkb) AS geom
FROM t
WHERE xmin <= :qmax_x AND xmax >= :qmin_x
  AND ymin <= :qmax_y AND ymax >= :qmin_y          -- prunes files at the manifest
  AND ST_Intersects(ST_GeomFromWKB(geom_wkb),       -- filters rows in survivors
                    ST_MakeEnvelope(:qmin_x, :qmin_y, :qmax_x, :qmax_y));
```

The bbox-cols predicate prunes files at the manifest level; the `ST_Intersects`
predicate filters rows inside the survivors. Engines don't yet auto-derive the
first from the second (same situation GeoParquet 1.1 users live with) — the spec
lists that as the one engine optimization worth adding, and it would benefit
GeoParquet 1.1 readers too.

It **composes** with GeoParquet: GeoIceberg V2 prunes *files* at the Iceberg
manifest; GeoParquet 1.1 prunes *row groups* inside each surviving file. Do both
and you get end-to-end fast bbox queries with zero engine code changes.

And it **migrates cleanly to V3**: when the engines you target ship working V3
geometry, `ALTER TABLE ADD COLUMN geom geometry(OGC:CRS84)`. The `geom_wkb` and
bbox columns stay; V3-aware engines read the typed column, V2-only engines keep
using bbox + WKB. The table stays portable for as long as you want.

## What to actually do if you're shipping geo on Iceberg today

- **V2 with flat bbox columns + WKB** — works on every engine that reads
  Iceberg V2, with provable file pruning. **Recommended.**
- **V2 with a GeoParquet-style bbox struct** — reads everywhere, but pruning is
  engine-dependent. Avoid for cross-engine portability.
- **V3 native geometry** — viable inside Snowflake (both managed *and*
  externally-written, as long as your writer emits the Snowflake-shape V3
  with `METADATA$RL_*` lineage cols, the right snapshot block, etc.). Other
  engines aren't there yet — most reject the type at parse, DuckDB reads but
  doesn't prune. So V3 is portable *as a producer* if you write the
  Snowflake-shape, and Snowflake will read it; but reading that same V3
  table elsewhere is still limited. V2 stays the right answer for full
  cross-engine portability. Also: V3 data files need native Parquet geometry
  typing (GeoParquet 2.0), not plain `BINARY`.

Track [duckdb-iceberg#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002)
/ [#1013](https://github.com/duckdb/duckdb-iceberg/pull/1013) and per-engine
V3 progress as the leading indicators — DuckDB is one decoder change away
from L3 now that the crash is gone; the others still need to accept the
type token first.

## Try it

```bash
git clone https://github.com/jatorre/iceberg-geo-testbed
cd iceberg-geo-testbed
brew install duckdb && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m testbed.v2_geo_convention   # the SPEC.md reference implementation
python engines/duckdb/run.py
```

Or point any engine that reads a static `metadata.json` at the public fixtures:

```
gs://cartobq-iceberg-geo-testbed/v2_geo_convention/metadata/v1.metadata.json
gs://cartobq-iceberg-geo-testbed/v3_geometry/metadata/v1.metadata.json
```

The matrices are kept current in [STATUS_V2.md](./STATUS_V2.md) (the convention)
and [STATUS_V3.md](./STATUS_V3.md) (native V3). If you're putting geo data into
Iceberg, try the V2 convention, file issues, and — if you maintain an engine —
the bbox-derivation optimization is one PR with a big payoff for both GeoParquet
and GeoIceberg readers.

---

**Repo**: [jatorre/iceberg-geo-testbed](https://github.com/jatorre/iceberg-geo-testbed)
· **Spec**: [SPEC.md](./SPEC.md)
· **Companion post** (can you *publish* open Iceberg at all?): [BLOG_CATALOG.md](./BLOG_CATALOG.md)
· **Independent cross-check**: [icebergmatrix.org](https://icebergmatrix.org/)
