"""Path-1 V2 test: have Snowflake itself write a managed V2 Iceberg table
laid out per the GeoIceberg V2 convention (bbox doubles + WKB BINARY), then
probe it.

Why this matters: the V2 convention is the portable bridge while V3 geometry
support is still landing across engines. A Snowflake-managed V2 table uses
*only* primitive Iceberg types (DOUBLE, BINARY, STRING) — no GEOMETRY type
token anywhere — so it should be readable by any engine that can read V2,
including ones whose parsers reject the V3 GEOMETRY type (BigQuery,
Databricks, Sedona). This table is the source we then federate into
Databricks via Snowflake Horizon.

The source data is the V2 `v2_geo_convention` table (already registered in
Snowflake at L3). We INSERT … SELECT the columns straight across — no type
conversion needed since both sides are bbox-doubles + WKB-binary.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "engines" / "snowflake"))

import snowflake.connector  # noqa: E402

from _creds import load  # noqa: E402


DB = "TESTBED"
SCHEMA = "PUBLIC2"
EXTERNAL_VOLUME = "ICEBERG_VOL_FRESH"
TABLE = "managed_v2_geo"
SOURCE = "v2_geo_convention"


def q(cur, sql_text: str, fetch: bool = True, limit: int = 20):
    label = sql_text.strip().split("\n", 1)[0][:100]
    print(f"\n>>> {label}")
    try:
        cur.execute(sql_text)
        if cur.description and fetch:
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(limit)
            if rows:
                print("    " + " | ".join(cols))
                for r in rows:
                    print("    " + " | ".join(str(v) for v in r))
            else:
                print("    (no rows)")
            return rows
        return None
    except Exception as e:
        msg = str(e).splitlines()
        for line in msg[:3]:
            print(f"    ERR: {line[:280]}")
        return None


def main() -> int:
    creds = load()
    conn = snowflake.connector.connect(
        **creds, role="ACCOUNTADMIN", warehouse="COMPUTE_WH",
        database=DB, schema=SCHEMA,
    )
    cur = conn.cursor()

    # Drop any prior test table
    q(cur, f"DROP TABLE IF EXISTS {TABLE}", fetch=False)

    # The V2 GeoIceberg-convention DDL. Pure primitive types — no GEOMETRY
    # token. ICEBERG_VERSION = 2 is the default but we set it explicitly so
    # the intent is unambiguous (and to contrast with the V3 sibling test).
    q(
        cur,
        f"""CREATE ICEBERG TABLE {TABLE} (
              id STRING,
              fp_xmin DOUBLE,
              fp_ymin DOUBLE,
              fp_xmax DOUBLE,
              fp_ymax DOUBLE,
              geom_wkb BINARY
            )
            EXTERNAL_VOLUME = '{EXTERNAL_VOLUME}'
            CATALOG = 'SNOWFLAKE'
            BASE_LOCATION = 'managed-v2-geo/'
            ICEBERG_VERSION = 2""",
        fetch=False,
    )

    # Inspect — is it V2? Schema as asked?
    q(cur, f"DESC TABLE {TABLE}")
    q(cur, f"SHOW PARAMETERS LIKE 'ICEBERG%' IN TABLE {TABLE}")

    # Load data from the V2 source table — straight column copy, no
    # conversion (both sides are bbox-doubles + WKB-binary).
    q(
        cur,
        f"""INSERT INTO {TABLE} (id, fp_xmin, fp_ymin, fp_xmax, fp_ymax, geom_wkb)
            SELECT id, fp_xmin, fp_ymin, fp_xmax, fp_ymax, geom_wkb
            FROM {SOURCE}""",
        fetch=False,
    )

    # Probe L1: full count
    q(cur, f"SELECT COUNT(*) FROM {TABLE}")

    # Probe L2/L3: bbox predicate — should return 196 rows (same as every
    # other engine's v2_geo_convention result) and prune at manifest level.
    q(
        cur,
        f"""SELECT COUNT(*) FROM {TABLE}
            WHERE fp_xmin <= -118 AND fp_xmax >= -125
              AND fp_ymin <= 40 AND fp_ymax >= 37""",
    )

    # Round-trip the WKB payload back to geometry text to prove the BINARY
    # column survived the managed write intact.
    q(
        cur,
        f"""SELECT id, ST_ASWKT(TO_GEOMETRY(geom_wkb)) AS wkt
            FROM {TABLE} LIMIT 5""",
    )

    # Bytes scanned for the bbox predicate (L3 inference)
    q(
        cur,
        f"""SELECT query_text, bytes_scanned
            FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY(RESULT_LIMIT => 10))
            WHERE query_text ILIKE '%fp_xmin <= -118%{TABLE}%'
              AND error_code IS NULL
            ORDER BY start_time DESC LIMIT 3""",
    )

    # Where did Snowflake store it on GCS? (so we can confirm Horizon serves
    # it and Databricks can federate it.)
    q(cur, f"SELECT SYSTEM$GET_ICEBERG_TABLE_INFORMATION('{TABLE}')")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
