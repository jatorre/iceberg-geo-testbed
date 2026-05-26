# Geo on Iceberg, today: a V2 convention while V3 catches up

*2026-05-26*

## The premise

Apache Iceberg V3 was announced in mid-2025 with native `geometry` and
`geography` types and per-file geometry bounds in the manifest. On
paper, this is the right architecture for the long-standing
"many-parquet-files, fast bbox query" problem in geospatial: instead
of every engine walking every file's footer to decide what to read,
Iceberg's manifest tells you *up front* which files can be skipped.

We built [`iceberg-geo-testbed`](https://github.com/jatorre/iceberg-geo-testbed)
to verify the promise — one repository where each major engine could
be probed against the same hand-written V2 / V3 fixtures, and the
results aggregated into a single matrix.

The headline finding: **no engine we tested supports V3 geometry
end-to-end as of mid-2026.** The closest is DuckDB, which parses the
type token but errors as soon as you read the column. Every other
engine rejects the type either at metadata parse or at SQL parse.

That left an open question: *what should you actually do today if you
have geospatial data and want fast Iceberg queries?*

This post is the answer, plus the proposal it suggests:
**GeoIceberg V2** — a recommended convention for storing geo data in
Iceberg V2 tables that delivers file-level pruning across every V2
engine, with a clean migration path to V3 when engines catch up. See
[`SPEC.md`](./SPEC.md) for the normative document.

## What we tested

Synthetic fixture: 10 geographically disjoint regions × 1000 random
points each = 10,000 rows across 10 parquet files in one Iceberg
table. A "California window" probe query should — with correct
file-level pruning — read 1 of 10 files. We measured the *highest
support level reached* per engine on an L0–L4 ladder:

- **L0** — table can't be read
- **L1** — full scan works
- **L2** — spatial predicate returns correct rows
- **L3** — file-level pruning works
- **L4** — row-group pruning (not currently measured)

Three table shapes:

- **V2 flat bbox columns** — `xmin/ymin/xmax/ymax` as flat `DOUBLE`
  columns at the schema root.
- **V2 bbox struct** — same data but inside a `bbox STRUCT<...>`
  column, GeoParquet-1.1-style.
- **V3 native geometry** — `geom GEOMETRY(OGC:CRS84)` per the V3 spec.

Each fixture was probed across six engines:

| Engine | V2 flat | V2 struct | V3 geometry |
|---|---|---|---|
| DuckDB 1.5.3 | **L3** | L2 (no pruning) | **L0** — type parses; `SELECT geom` errors with `BLOB→GEOMETRY` cast missing, bound deserializer missing |
| BigQuery / BigLake | **L3** | **L3** | **L0** — `Unknown Iceberg type "geometry(OGC:CRS84)"` |
| Sedona / Iceberg-Spark 1.7.1 | **L3** | **L3** | **L0** — type rejected at parse + UDT writer missing |
| Snowflake | blocked | blocked | blocked | (account-side bug, support ticket pending) |
| Databricks (DBSQL 2026.10) | n/a here* | n/a* | **L0** — `[UNSUPPORTED_DATATYPE]` |
| Oracle ADB 26ai | L0** | L0** | L0** | (\**rejects pyiceberg-emitted manifests at parser layer) |

\* Databricks fully supports V2 reads via Unity Catalog, Glue, HMS, or
Snowflake Horizon — but has no generic Iceberg REST consumer and no
"static metadata.json on a bucket" path. Our public-bucket testbed
just doesn't slot into Databricks's catalog-mediated reader. Per
icebergmatrix.org confirmed.

## The interesting findings beyond "V3 doesn't work"

**V2 struct-field pruning is engine-dependent.** DuckDB reads the
`bbox.xmin` predicate but doesn't push it to manifest bounds — all 10
files scanned. BigQuery and Sedona prune the same predicate to 1
file. So "GeoParquet-1.1-style bbox struct" is *not* a portable
pruning strategy for Iceberg. The flat-column version is.

**Our hand-written V2 metadata is fully spec-compliant.** We deployed
Apache Polaris (the reference Iceberg REST catalog) on a small GCE VM
and registered all fixtures against it. Polaris accepted V2 fixtures
cleanly. (V3 initially failed with `Cannot parse missing long:
next-row-id` — a pyiceberg 0.11.1 gap that we patched in our static
catalog writer.) **Oracle's rejection isn't about our metadata; it's
that Oracle's reader is stricter than the spec.**

**DuckDB's V3 support claim is overstated.** The
[icebergmatrix.org](https://icebergmatrix.org) compatibility matrix
shows DuckDB V3 geometry as `full`. Our hands-on found it's L0 — the
type token is recognized but two distinct gaps (manifest-bound
deserializer + parquet BLOB→GEOMETRY cast) block actual reads. Filed
back as [duckdb-iceberg#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002).

**Databricks has explicit, intentional gaps in Iceberg interop.**
Their Lakehouse Federation supports Unity Catalog, AWS Glue, HMS, and
Snowflake Horizon as Iceberg sources. No generic Iceberg REST. No
static metadata.json. No Polaris. The `CREATE CONNECTION TYPE iceberg`
literally errors with `CONNECTION_TYPE_NOT_SUPPORTED`. Their `full`
REST catalog support claim is the *serve* side (Unity Catalog hosting
the REST API), not the consume side.

**Oracle ADB isn't in icebergmatrix.org at all.** Their reader is
strict enough to reject pyiceberg-emitted manifests that every other
engine accepts. We may have written the first cross-engine
documentation of that behavior.

## The historical parallel: GeoParquet 1.1

This story has happened before, in the layer below.

Parquet did not natively understand "geometry" for years. The
community defined **GeoParquet 1.1** as a *convention* layered on top
of Parquet: a covering `bbox` struct (per row group) + a `geometry`
WKB binary column + a `geo` metadata block in the parquet footer
declaring CRS, encoding, and which column is which. Engines that
understood the convention could prune row groups against the bbox
struct's column statistics; engines that didn't could still read the
file as plain Parquet.

When Parquet later gained native geometry types, **GeoParquet 2.0**
was the migration target — the convention had defined the data model
clearly enough that the spec just absorbed it.

Iceberg is at exactly the same point Parquet was three years ago. V3
geometry is the eventual target, but the ecosystem isn't there yet.

## The proposal: GeoIceberg V2

The full normative document is at [`SPEC.md`](./SPEC.md). The shape:

A GeoIceberg V2 table includes:

- A WKB binary column carrying the geometry payload
- Four `DOUBLE` columns (`xmin, ymin, xmax, ymax`) carrying the
  per-row bounding rectangle of that geometry
- An Iceberg table property named `geo`, JSON-encoded, mirroring
  GeoParquet 1.1's structure:

```json
{
  "version": "1.0",
  "primary_column": "geom_wkb",
  "columns": {
    "geom_wkb": {
      "encoding": "WKB",
      "crs": "OGC:CRS84",
      "edges": "planar",
      "bbox_columns": ["xmin", "ymin", "xmax", "ymax"]
    }
  }
}
```

Column names are free-form; the `geo` property is the source of
truth. Standard Iceberg V2 writers automatically populate per-file
manifest bounds on `DOUBLE` columns, so file pruning comes for free
once the bbox columns are present.

The user-facing query pattern is exactly what GeoParquet 1.1 users
already write:

```sql
SELECT id, ST_GeomFromWKB(geom_wkb) AS geom
FROM table
WHERE xmin <= :qmax_x AND xmax >= :qmin_x
  AND ymin <= :qmax_y AND ymax >= :qmin_y
  AND ST_Intersects(ST_GeomFromWKB(geom_wkb),
                    ST_MakeEnvelope(:qmin_x, :qmin_y, :qmax_x, :qmax_y));
```

The bbox-cols predicate prunes at the Iceberg manifest level (file
selection); the `ST_Intersects` predicate filters rows inside
surviving files. Engines today don't auto-derive the first from the
second — same situation GeoParquet 1.1 users live with — and the spec
explicitly lists this as the engine optimization to add.

### The two-layer composition

GeoIceberg V2 and GeoParquet 1.1 are *complementary*, not competing:

- GeoIceberg V2 prunes **files** at the Iceberg manifest level
- GeoParquet 1.1 prunes **row groups** inside each surviving file

Following both gives you end-to-end fast bbox queries with no engine
code changes today.

### Migration to V3

When the engines you care about ship working V3 geometry support, you
`ALTER TABLE ADD COLUMN geom geometry(OGC:CRS84)`. The existing
`geom_wkb` and bbox columns stay. V3-aware engines read the typed
column; V2-only engines keep using bbox cols + WKB. The table stays
portable for as long as you want.

## What this means if you're shipping geo data on Iceberg

If you're choosing a format today, the testbed results are clear:

- **V2 with flat bbox columns + WKB**: works on every engine that can
  read static metadata.json. Provable file pruning. Recommended.
- **V2 with the GeoParquet-style bbox struct**: works for reads, but
  pruning is engine-dependent (DuckDB doesn't, BigQuery and Sedona
  do). Avoid for cross-engine portability.
- **V3 native geometry**: not viable on any engine end-to-end yet.
  Track the [DuckDB issue we filed](https://github.com/duckdb/duckdb-iceberg/issues/1002)
  and Snowflake's preview as the leading indicators.

The repo's matrix is kept up to date — re-run the engine probes
whenever you want a fresh reading.

## Why we're publishing this

Three reasons:

1. **The matrix is independently valuable.** Engineering teams
   evaluating Iceberg geospatial support shouldn't have to rediscover
   what we just spent a week finding. The repo is reproducible: each
   engine has a runner, the fixtures are in a public GCS bucket, and
   `python engines/<engine>/run.py` gives you the matrix row.
2. **GeoIceberg V2 needs adopters and a community.** This isn't a
   formal Apache spec yet — it's a proposal grounded in empirical
   evidence. If your team is putting geo data into Iceberg, please
   try it, file issues, propose changes. The path to a real
   community-accepted convention runs through people using it.
3. **We want engines to add the bbox-derivation optimization.** It
   benefits both GeoParquet 1.1 and GeoIceberg V2 readers. One PR per
   engine, big payoff. The
   [duckdb-iceberg issue](https://github.com/duckdb/duckdb-iceberg/issues/1002)
   is the model for what we'd file.

## Try the testbed

```bash
git clone https://github.com/jatorre/iceberg-geo-testbed
cd iceberg-geo-testbed
brew install duckdb && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m testbed.v2_geo_convention
python engines/duckdb/run.py
```

Or for the cloud engines, the same fixtures are available at:

```
gs://cartobq-iceberg-geo-testbed/v2_geo_convention/metadata/v1.metadata.json
```

(publicly readable; usable from any engine that accepts `https://`-style
static-metadata Iceberg URLs).

---

**Repo**: [jatorre/iceberg-geo-testbed](https://github.com/jatorre/iceberg-geo-testbed)
**Spec**: [SPEC.md](./SPEC.md)
**Matrix**: [README.md](./README.md)
**Independent cross-check**: [icebergmatrix.org](https://icebergmatrix.org/)
