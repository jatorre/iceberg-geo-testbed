# Databricks engine runner

Status as of **2026-05-23.** Databricks SQL (DBSQL `2026.10`) on a
GCP-hosted workspace, authenticating via REST token + warehouse ID from
the `carto-dev-database-credentials` gcloud secret.

## Headline finding

Databricks **cannot read our hand-written static `metadata.json`** (the
mode all the other engines we tested support). It requires a catalog
server (Unity Catalog, Iceberg REST, AWS Glue, or Snowflake Horizon) to
mediate Iceberg table discovery. There is no `CREATE TABLE ... USING
ICEBERG LOCATION 'gs://.../v1.metadata.json'` path; the closest syntax
silently creates an empty Delta table instead.

> *"Iceberg tables in Unity Catalog do not support a LOCATION clause."*
> — [Databricks docs on Iceberg](https://docs.databricks.com/aws/en/iceberg/)

So the "point at our metadata, see what happens" probe we ran against
DuckDB / BigQuery / Sedona is structurally unavailable here.

## What we did test: Databricks-managed V3

To still get a meaningful Databricks row in the matrix, we tried having
Databricks **write its own** V3 Iceberg table with a `GEOMETRY` column.

```sql
CREATE TABLE iceberg_geo_testbed_v3 (
  id STRING,
  geom GEOMETRY
) USING ICEBERG
TBLPROPERTIES ('format-version'='3');

-- [UNSUPPORTED_DATATYPE] Unsupported data type "GEOMETRY".
-- SQLSTATE: 0A000
```

Same result for `GEOGRAPHY`. So Databricks SQL's parser doesn't recognize
the V3 geospatial type tokens as of DBSQL `2026.10`. This matches the
state in BigQuery and Sedona/Iceberg-Spark.

What *does* work:

- `CREATE TABLE … USING ICEBERG TBLPROPERTIES ('format-version'='3')`
  with non-geometry columns (so the V3 framework is enabled — VARIANT,
  deletion vectors, row IDs from the V3 preview).
- The `ST_*` family of spatial **functions** (`st_point`, `st_area`,
  `st_intersects`, `st_geomfromtext`, …). But they return WKT/WKB
  **strings**, not a typed Geometry value — there's no UDT registered.

So Databricks has the spatial *function library* but not the V3 typed
geo-column primitives that the Iceberg V3 spec relies on for per-file
bounds + pruning.

## Matrix row

| Fixture | Level | Detail |
|---|---|---|
| `v2_flat_columns` | n/a | Can't read our metadata; structural blocker. Could be created from scratch in a Databricks-managed table, but that's no longer an interop test. |
| `v2_bbox_struct`  | n/a | same |
| `v3_geometry`     | **L0** | Even self-managed: `GEOMETRY` (and `GEOGRAPHY`) types rejected by DBSQL parser. |

## What would unblock it

To get a non-blocked Databricks row testing OUR metadata, we'd need to:

1. Stand up an Iceberg REST catalog (Tabular/Polaris/etc.) serving our
   metadata.
2. Register it as a foreign catalog in Databricks via Lakehouse Federation.
3. Then `CREATE FOREIGN TABLE` against it.

That's a real chunk of work for a single matrix row, and the V3
geometry result would still be L0 because the type isn't recognized in
the parser regardless. Worth revisiting once Databricks announces V3
geospatial GA.

## Files

- `_creds.py` — REST credentials loader (gcloud secret or env override).
- `_discover.py` — read-only state probe (catalogs, schemas, storage
  credentials, external locations).
- `_first_probe.py` — surfaced the static-metadata-read structural blocker.
- `_managed_v3.py` — surfaced the `UNSUPPORTED_DATATYPE: GEOMETRY` finding.
