# Geo on Iceberg, today: a V2 convention while V3 catches up

*2026-05-27*

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

The headline finding: **only Snowflake delivers V3 geometry end-to-end
— and only for tables it manages itself.** Every other engine sits
somewhere between "rejects the type outright" (BigQuery, Sedona,
Databricks, Oracle) and "reads the column but can't prune on it"
(DuckDB, at L2). And crucially, *no* engine — Snowflake included — can
yet read a **portable, externally-written** V3 geometry table. The V3
support that exists is each vendor reading its own managed output.

So if you want geospatial data on Iceberg that *any* engine can query
today, V3 isn't the answer yet.

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
| DuckDB 1.5.3 | **L3** | L2 (no struct pruning) | **L2** — type parses, `ST_AsText(geom)` returns clean WKT; manifest bound deserializer has no GEOMETRY branch, so `ST_Intersects` crashes and L3 pruning is blocked ([#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002)) |
| BigQuery / BigLake | **L3** | **L3** | **L0** — `Unknown Iceberg type "geometry(OGC:CRS84)"` |
| Snowflake (GA May 2026) | **L3** | **L3** | **L3 (managed)** — the only engine where V3 geometry delivers end-to-end: spatial predicate correct *and* manifest geometry-bound pruning fires. But only for Snowflake-managed tables; its V3 *unmanaged* (external-metadata) read is still broadly non-functional |
| Sedona / Iceberg-Spark 1.7.1 | **L3** | **L3** | **L0** — type rejected at parse + UDT writer missing |
| Databricks (DBSQL 2026.10) | **L2** via Snowflake federation* | **L2** via federation* | **L0** — `GEOMETRY(SRID)`/`GEOGRAPHY(SRID)` work in **Delta**, but the Iceberg-compat writer (`IcebergWriterCompatV1`/`V3`) rejects them; geo-in-Iceberg likely coming soon |
| Oracle ADB 26ai | **L0** | **L0** | **L0** — `ORA-20000: Failed to generate column list`; fails regardless of storage, auth, producer, or metrics (see below) |

\* Databricks has no generic Iceberg-REST consumer and no
"static metadata.json on a bucket" path — only named catalogs
(Glue / HMS / Snowflake Horizon / Unity) can back a foreign Iceberg
table. We reached our V2 data by federating a Snowflake-managed copy
via `CREATE CONNECTION TYPE snowflake` (query federation; the
direct-from-storage read is blocked separately — see below).

## The interesting findings beyond "V3 isn't ready"

**Snowflake's managed V3 geometry actually works — proof the
architecture is sound.** A Snowflake-managed `GEOMETRY` table
(`ICEBERG_VERSION=3` is required; the default is V2, and the error if
you forget doesn't hint at it) returns correct spatial-predicate results
*and* prunes on the manifest's geometry bounds (`bytes_scanned=0` on the
spatial query). So per-file geometry bounds aren't vaporware — when an
engine writes and reads its own V3, the promise holds. The catch is the
word "own": Snowflake can't yet read an externally-written V3 table, and
neither can anyone else.

**V3 geometry needs GeoParquet-2.0-style native typing in the data
files — plain WKB-in-`BINARY` isn't enough.** We learned this the hard
way: DuckDB jumped from L0 to L2 the moment we wrote the geometry column
with a native Parquet `Geometry` logical type (via geoarrow-pyarrow)
instead of a plain `BINARY` column. Iceberg V3 geometry maps to the
Parquet native Geometry logical type that GeoParquet 2.0 standardizes —
an engine won't recognize a `BINARY` column as geometry no matter what
the Iceberg schema claims.

**DuckDB is L2 — not the `full` the matrix claims, but not broken
either.** [icebergmatrix.org](https://icebergmatrix.org) lists DuckDB V3
geometry as `full`. Hands-on: the type parses, and `SELECT geom` /
`ST_AsText(geom)` return clean geometries — but a spatial *predicate*
trips a manifest bound deserializer that has no `GEOMETRY` branch and
crashes. One well-isolated gap between L2 and L3 pruning. We filed it as
[duckdb-iceberg#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002);
the maintainer confirmed manifest bounds-stats handling is the remaining
piece.

**V2 struct-field pruning is engine-dependent.** DuckDB reads the
`bbox.xmin` predicate but doesn't push it to manifest bounds — all 10
files scanned. BigQuery and Sedona prune the same predicate to 1 file.
So a "GeoParquet-1.1-style bbox struct" is *not* a portable pruning
strategy for Iceberg; flat top-level bbox columns are.

**Our metadata is spec-compliant by the reference catalog.** Apache
Polaris (the reference open-source Iceberg REST catalog) accepts all our
V2 fixtures, and our V3 too after we patched a real pyiceberg gap it
caught (`next-row-id` / `row-lineage`). So whenever an engine rejects our
tables, it's the engine being stricter than the spec — not us.

**Databricks has two separate Iceberg gaps, both worth knowing.**
(1) Geo types *exist* — `GEOMETRY(SRID)` / `GEOGRAPHY(SRID)` are valid
**Delta** column types — but the Iceberg-compat writer rejects them
(`DELTA_ICEBERG_WRITER_COMPAT_VIOLATION`). That error name is the tell:
Databricks "Iceberg" is Delta plus an Iceberg-compat writer, so geo stops
at that boundary. Likely coming soon. (2) Structurally, Databricks has
**no generic Iceberg-REST consumer — by design** — `CREATE CONNECTION
TYPE iceberg` errors `CONNECTION_TYPE_NOT_SUPPORTED`; only Glue, HMS,
Snowflake Horizon, and Unity-to-Unity can back a foreign Iceberg table.
This is a deliberate choice, not a temporary gap: Databricks certifies
per-partner IRC connectors (its Lakehouse Federation model, the same
approach Glue / Fabric / BigQuery take) rather than accepting any
spec-compliant endpoint, and prioritizes new ones by customer demand. So
conformance to the spec isn't sufficient for access, and you can't point
it at a self-hosted Polaris/Nessie/Lakekeeper even once geo lands. Its
`full` REST-catalog claim is the *serve* side (Unity hosting the REST
API), not the consume side. The reliability rationale is real — this
testbed shows IRC implementations genuinely diverge — but it leaves the
open standard practically gated at the connector layer. We *did* reach our V2 data
by federating a Snowflake-managed copy (query federation via
`TYPE snowflake`); the direct-from-storage read fell back to JDBC because
Databricks rejects Snowflake-on-GCP's `gcs://` metadata scheme (it takes
`gs://`, not `gcs://`).

**Oracle's wall is its Iceberg reader itself — not our metadata, not
storage.** Oracle ADB rejects the table with `ORA-20000: Failed to
generate column list`. We chased it hard and ruled out every external
variable: adding the optional manifest metrics didn't help; **Snowflake's
own Spark-lineage metadata fails identically**; and after staging the
same fixture to **S3 with a working IAM credential** (Oracle's
`LIST_OBJECTS` succeeded), the Iceberg read *still* failed the same way.
So it's neither storage (GCS vs S3) nor producer nor auth — it's Oracle's
direct-`metadata.json` Iceberg path. Oracle isn't in icebergmatrix.org;
this may be the first cross-engine documentation of the behavior.

**The deeper lesson: interop is governed by storage × catalog × auth,
not just engine × format.** Whether a read works *at all* turned out to
depend on three orthogonal axes as much as the format version:
**storage backend** (S3 is first-class; GCS is second-class in several
engines — Databricks's direct read even rejects the `gcs://` scheme),
**catalog mechanism** (static `metadata.json` vs generic Iceberg REST vs
named catalogs — engines support wildly different subsets), and **auth
mode** (public, credential-vended, keyed-vs-keyless, and long-lived-vs-
temporary — Oracle's S3 credential rejects temporary STS session tokens
outright). A corollary that bites everyone: many connectors have no
first-class anonymous path, so you must hand them an *empty* credential
just to read a public bucket. "Snowflake-on-GCS" and "Snowflake-on-AWS"
are genuinely different cells.

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
- **V3 native geometry**: viable **only inside a single managed platform
  today**. Snowflake-managed V3 works end-to-end, but no engine can read
  another engine's externally-written V3 yet — so it's not a portable
  choice. If you're all-in on one vendor that supports it, use it; if you
  need cross-engine reads, V2 is still the answer. And remember V3 data
  files need native Parquet geometry typing (GeoParquet 2.0), not plain
  `BINARY`. Track the
  [DuckDB issue we filed](https://github.com/duckdb/duckdb-iceberg/issues/1002)
  and per-engine V3 progress as the leading indicators.

And don't reason about the format in isolation: **where you host it
(S3 vs GCS), how the table is announced (static `metadata.json` vs a
catalog), and the auth mode all gate which engines can read it** — often
more decisively than the format version. (S3 + a named catalog is the
widest-compatibility combination today.)

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
