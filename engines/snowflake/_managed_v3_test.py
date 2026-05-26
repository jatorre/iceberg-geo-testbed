"""Path-1 V3 geometry test: have Snowflake itself write a managed V3
Iceberg table with a native GEOMETRY column, then probe it.

Two goals:
  1. See whether Snowflake's claimed `full` V3 geometry support actually
     delivers — does CREATE TABLE accept GEOMETRY in DDL? Does
     ST_INTERSECTS prune at file level? Does pruning work in V3 the
     same way it does in V2?
  2. Inspect the V3 metadata.json + manifest avro that Snowflake writes
     to GCS, so we can learn what a "good" V3 fixture looks like and
     iterate our hand-written V3 writer to match.

The source data is the V2 `v2_geo_convention` table (already registered
in Snowflake at L3). We INSERT … SELECT to copy the WKB column into a
fresh GEOMETRY column, letting Snowflake do the WKB→GEOMETRY conversion
in-flight.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "engines" / "snowflake"))

import snowflake.connector  # noqa: E402

from _creds import load  # noqa: E402


DB = "TESTBED"
SCHEMA = "PUBLIC2"
EXTERNAL_VOLUME = "ICEBERG_VOL_FRESH"
TABLE = "managed_v3_geo"


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

    # The V3 + GEOMETRY DDL.
    # *** Critical: ICEBERG_VERSION = 3 is required. ***
    # Default ICEBERG_VERSION is 2; V2 explicitly rejects GEOMETRY/GEOGRAPHY
    # for Iceberg tables ("Unsupported data type 'GEOMETRY' for iceberg
    # tables"). The error message doesn't hint at V3 being the fix —
    # finding this requires reading the release notes or trying
    # variants. Worth flagging to Snowflake for a better error.
    q(
        cur,
        f"""CREATE ICEBERG TABLE {TABLE} (
              id STRING,
              geom GEOMETRY
            )
            EXTERNAL_VOLUME = '{EXTERNAL_VOLUME}'
            CATALOG = 'SNOWFLAKE'
            BASE_LOCATION = 'managed-v3-geo/'
            ICEBERG_VERSION = 3""",
        fetch=False,
    )

    # Inspect the table — is it actually V3? Schema what we asked for?
    q(cur, f"DESC TABLE {TABLE}")
    q(
        cur,
        f"""SHOW PARAMETERS LIKE 'ICEBERG%' IN TABLE {TABLE}""",
    )

    # Load data from the V2 source table
    q(
        cur,
        f"""INSERT INTO {TABLE} (id, geom)
            SELECT id, TO_GEOMETRY(geom_wkb)
            FROM v2_geo_convention""",
        fetch=False,
    )

    # Now probe
    q(cur, f"SELECT COUNT(*) FROM {TABLE}")
    q(
        cur,
        f"""SELECT COUNT(*) FROM {TABLE}
            WHERE ST_INTERSECTS(
              geom,
              TO_GEOMETRY('POLYGON((-125 32, -115 32, -115 42, -125 42, -125 32))')
            )""",
    )

    # Bytes scanned for the predicate (L3 inference)
    q(
        cur,
        f"""SELECT query_text, bytes_scanned
            FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY(RESULT_LIMIT => 5))
            WHERE query_text ILIKE '%ST_INTERSECTS%{TABLE}%'
              AND error_code IS NULL
            ORDER BY start_time DESC LIMIT 3""",
    )

    # The big payoff: see where Snowflake stored the table on GCS and
    # what the metadata looks like.
    q(
        cur,
        f"""SELECT SYSTEM$GET_ICEBERG_TABLE_INFORMATION('{TABLE}')""",
    )

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
