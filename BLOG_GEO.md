# Apache Iceberg V3 + GeoParquet 2.0: a year in, where we actually stand

*2026-05-28*

When [Apache Iceberg V3 was approved in mid-2025](https://iceberg.apache.org/spec/#version-3) it was a big moment for anyone building a serious geospatial stack on lakehouses. At CARTO [we wrote about why](<!-- TODO: paste CARTO blog URL here -->) — V3 introduced **native `geometry` and `geography` types with per-file geometry bounds in the manifest**, the right architecture for the long-standing "too many parquet files, slow bbox queries" problem. At nearly the same time, the Apache Parquet project shipped a new release adding **native Parquet geometry logical types** — what GeoParquet 2.0 standardizes. Together they promised something the open data ecosystem had been missing: a full stack of open table formats that understand geospatial data natively.

We knew engine support wouldn't land the day the spec was approved. Almost a year on, we thought it was time to actually run the experiments.

## What we did

We built [`iceberg-geo-testbed`](https://github.com/jatorre/iceberg-geo-testbed) — a small, public repo with a reproducible set of geospatial Iceberg fixtures and per-engine probes:

- A **static Iceberg REST catalog** (the "Portolan" pattern — pre-rendered JSON on a bucket) exposing both V2 and V3 tables, served public on GCS and S3.
- **GeoParquet 2.0–typed parquet files** as the data layer — geometry as Parquet's native `Geometry(crs=)` logical type, not plain `BINARY`. (Iceberg V3 geometry is GeoParquet 2.0 typing at the file level + Iceberg manifest bounds at the table level. The two specs are coupled.)
- **Per-engine runners** that probe each table the way real users would: open it, scan it, run a spatial predicate, see if pruning fires.

Each fixture has the same data: 10 geographically disjoint regions × 1000 points = 10,000 rows across 10 parquet files. A California-window predicate gives a clean signal — a fully-pruning engine should narrow to 1 file out of 10.

We graded each engine on a small ladder:

- **L0** — table can't be read at all
- **L1** — full scan works
- **L2** — spatial predicate returns correct rows
- **L3** — file-level pruning fires (the manifest narrows the scan)

## Where V3 native geometry stands

| Engine | V3 native geometry |
|---|---|
| **Snowflake** (GA May 2026) | **L3 — works end-to-end.** Managed and externally-written paths both deliver correct spatial predicates *and* manifest geometry-bound pruning. The clear leader. |
| **DuckDB 1.5.3** | **L2** — reads correctly; spatial predicates work with [PR #1013](https://github.com/duckdb/duckdb-iceberg/pull/1013) (in flight). File pruning is the next step. |
| **BigQuery / BigLake, Dataproc Serverless 2.3, EMR Serverless 7.13** | **L0 — V3 available, no geometry/geography data type yet.** We verified all three read a non-geometry V3 fixture cleanly (10000 rows back). The geometry column is the specific implementation gap. The two Spark variants share one upstream `iceberg-spark-runtime` cause. |
| **Sedona + Iceberg-Spark 1.7.1** | **L0** — same upstream `iceberg-spark-runtime` gap; also can't *write* V3 geometry (Sedona UDT mapper missing). |
| **Databricks** | **L0** — `GEOMETRY/GEOGRAPHY` work in Delta, but the Iceberg-compat writer rejects them. Likely coming soon. |
| **Oracle ADB 26ai** | **L0** — Oracle's Iceberg reader can't read any of our Iceberg tables at all (V2 or V3, ours or Snowflake's). Reader-side bug, upstream of V3. |

Detail and per-capability breakdowns live in [`STATUS_V3.md`](./STATUS_V3.md).

## The two engines doing it right today

### Snowflake — the current leader

Snowflake delivers Iceberg V3 geometry **end-to-end**, on both its managed write path *and* on externally-written V3 tables. We verified the whole stack:

- Spatial predicates return correct rows.
- Manifest geometry-bound pruning fires — `bytes_scanned` for a California-window predicate is roughly 1/10 of a full GEOM-column scan on a 10-file fixture. Exactly what file pruning should look like.
- The unmanaged read path works once your writer is **V3-spec-compliant** (the snapshot block needs `first-row-id` + `added-rows`, and the manifest needs populated `value_counts` / `null_value_counts` and ID-column bounds). No Snowflake-specific shape required — just the spec.

Two practical notes if you try this yourself: for managed tables, pass `ICEBERG_VERSION=3` explicitly (V2 is the default for new Iceberg tables, and the error if you forget doesn't hint at the opt-in). For queries, use `TO_GEOMETRY(wkt, 4326)` rather than bare `TO_GEOMETRY(wkt)` — the GEOM column is SRID 4326, and a bare envelope defaults to SRID 0 and fails the predicate with `Incompatible SRID: 4326 and 0`.

### DuckDB — very close

DuckDB 1.5.3 reads V3 geometry correctly: the type parses, `ST_AsText(geom)` returns clean WKT. On stock 1.5.3 a spatial *predicate* crashes a manifest bound deserializer — we filed [duckdb-iceberg#1002](https://github.com/duckdb/duckdb-iceberg/issues/1002), and the maintainer responded fast with [PR #1013](https://github.com/duckdb/duckdb-iceberg/pull/1013). We built it locally and verified the crash is gone — spatial predicates now return correct rows on both our fixture and Snowflake's own managed V3 table.

The PR deliberately *skips* decoding the geometry bound, so DuckDB falls back to a full scan rather than pruning on it — that's L2. Closing to L3 needs one more change: actually decoding the V3 `packed_xy_le` manifest bound and feeding it into the file-pruning predicate. The encoding is documented in the repo; the path from L2 to L3 is short.

## Brief notes on the rest

**The sharpest read on what's missing:** these engines *do* support V3 — they just don't support V3 *geometry* yet. To check, we built a minimal V3 fixture with no geometry columns (`id` STRING + `n` INT) and re-ran the probes. BigQuery, Dataproc Serverless 2.3, and EMR Serverless 7.13 all read it cleanly — 10000 rows back, schema correct. So V3 has rolled out on all three; the specific gap is the geometry-type implementation. That's a much stronger signal than "they don't support V3" — it says **geometry isn't on the near-term roadmap on these engines.**

**The three Spark variants** (Sedona + Iceberg-Spark 1.7.1, Dataproc 2.3, EMR Serverless 7.13) fail identically on the *geometry* fixture with `UnsupportedOperationException: Cannot convert unknown type to Spark: geometry`. The gap is upstream in `iceberg-spark-runtime` — *one* PR there would move all three. icebergmatrix.org lists "EMR (8.0 Spark): Full" for V3 Geometry, but `emr-8.x` doesn't actually exist yet as a release label (`aws emr list-release-labels` only goes up to `emr-7.13.0`); the latest testable EMR is L0 on geometry.

**BigQuery** rejects the V3 geometry type specifically at `CREATE EXTERNAL TABLE` (`Unknown Iceberg type "geometry(OGC:CRS84)"`). The same `CREATE EXTERNAL TABLE` pattern works fine on the non-geometry V3 fixture, confirming the V3 reader is real and the gap is the geometry type.

**Databricks** is the most asymmetric case: `GEOMETRY(SRID)` and `GEOGRAPHY(SRID)` already work in *Delta*, but the Iceberg-compat writer rejects them (`DELTA_ICEBERG_WRITER_COMPAT_VIOLATION`). The geometry types exist on the platform; they just don't cross the Delta→Iceberg boundary yet. **Likely coming soon.**

**Oracle ADB** sits outside this whole conversation — blocked upstream of V3. `DBMS_CLOUD.CREATE_EXTERNAL_TABLE` errors `ORA-20000: Failed to generate column list` on every Iceberg table we've pointed it at — ours, Snowflake's Spark-lineage output, V2, V3, all identical. We've ruled out storage, producer, metrics, and auth. It's the reader, and the V3 question can't even be asked until it can read V2.

---

> **Footnote — what about Iceberg V2?** You *can* do geospatial on V2 today with a convention we've written up as [GeoIceberg V2](./SPEC.md): flat `double` bbox columns + a WKB column + a `geo` table property. It works on every engine that reads V2, and you get file-level pruning for free since standard Iceberg writers record manifest bounds on `double` columns. We've kept V2 out of the focus of this post because V3 is the destination: the spec is right, one major engine delivers it end-to-end, one major OSS engine is one PR away, and the rest will follow. The V2 convention is the bridge, not the destination.

---

## A bigger problem: not everyone can read open Iceberg catalogs

One thing we hit along the way, orthogonal to geometry: even when an engine *could* read an Iceberg table, it often **can't reach an open catalog** to discover the tables. The clearest example is Databricks — there's no generic Iceberg-REST consumer (`CREATE CONNECTION TYPE iceberg` returns `CONNECTION_TYPE_NOT_SUPPORTED`); only named partners (Glue, HMS, Snowflake, Databricks itself) can back a foreign Iceberg table. Other warehouses have variants of the same constraint: their connectors *mandate* authentication that a public bucket can't satisfy.

This deserves a post of its own — and we wrote one: see **[BLOG_CATALOG.md](./BLOG_CATALOG.md)** for the full analysis, plus the Cloudflare Worker we built that bridges the gap for warehouses that can't read static catalogs directly.

## Recommendations

If you're picking a format for geospatial today:

- **Files on disk → GeoParquet 2.0** (native Parquet geometry typing). Mature, broadly readable. Use it.
- **A table you'll query through Snowflake → V3 directly.** Snowflake delivers the full pruning story end-to-end today, on both managed and externally-written V3. Ship it.
- **A table you need many engines to read today → the V2 convention.** Flat bbox columns + WKB + `geo` table property. File-level pruning on every engine that reads V2 Iceberg. Bridge, not destination.
- **A table you'll keep for years → V3 is the right target.** The architecture is sound (we proved it), DuckDB is one change away, Databricks is reportedly close, the rest will follow. If your timeline can absorb the engine catch-up, V3 is worth the wait.

## Why this matters

A few years ago you wrote bespoke code for every geospatial query because every engine read storage differently and nobody indexed bounding boxes consistently. The promise of GeoParquet 2.0 + Iceberg V3 is exactly what the analytics community already enjoys for non-spatial data: **open formats, remote catalogs, automatic file pruning, predictable performance** — for geometry too.

We're not all the way there yet. But for the first time we can point an arbitrary engine at a *public* Iceberg V3 dataset with geometry columns, run a spatial predicate, and get the right answer back — and on one engine, with real file-level pruning over the manifest's geometry bounds.

**Big kudos to Snowflake for being the first to actually ship it end-to-end.** The hard part — proving the architecture works — is done. Now the rest of the ecosystem catches up.

## Try it

Everything in this post is reproducible from the repo. The fixtures live on a public GCS bucket; you can point any engine at them.

```bash
git clone https://github.com/jatorre/iceberg-geo-testbed
cd iceberg-geo-testbed
brew install duckdb && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m testbed.v3_geometry            # the V3 reference fixture
python engines/duckdb/run.py
```

Or point any engine that reads a static `metadata.json` at the public fixtures:

```
gs://cartobq-iceberg-geo-testbed/v3_geometry/metadata/v1.metadata.json
```

The matrices stay current in [STATUS_V3.md](./STATUS_V3.md). If you maintain an engine and want to flip a cell, the bbox-derivation optimization and the geometry-bound deserializer are both well-isolated PRs with big payoffs.

---

**Repo**: [jatorre/iceberg-geo-testbed](https://github.com/jatorre/iceberg-geo-testbed)
· **Spec for the V2 bridge**: [SPEC.md](./SPEC.md)
· **Status matrix (V3)**: [STATUS_V3.md](./STATUS_V3.md)
· **Companion post** (can you *publish* open Iceberg at all?): [BLOG_CATALOG.md](./BLOG_CATALOG.md)
· **Independent cross-check**: [icebergmatrix.org](https://icebergmatrix.org/)
