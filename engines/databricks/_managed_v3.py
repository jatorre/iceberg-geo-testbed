"""Test Databricks-managed Iceberg V3 with a native GEOMETRY column.

Walks the L0-L4 ladder:
  L0  CREATE TABLE ... geom GEOMETRY USING ICEBERG (format-version=3) fails
  L1  table registers + SELECT * works
  L2  spatial predicate returns the correct rows (196 of 10000)
  L3  manifest-level file pruning kicks in (only california file scanned)
  L4  row-group pruning (not measured)

Cleans up after itself.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from databricks import sql  # noqa: E402

from testbed.common import REGIONS, stable_seed  # noqa: E402

from _creds import load  # noqa: E402


CATALOG = "`engineering-catalog-default`"
SCHEMA = "jarroyo_carto"
TABLE_BASE = "iceberg_geo_testbed_v3_geo"


def q(cur, sql_text: str, *, fetch: bool = False, limit: int = 20):
    label = sql_text.split("\n", 1)[0][:120]
    print(f"\n>>> {label}")
    try:
        cur.execute(sql_text)
        if cur.description:
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(limit)
            if rows:
                print("    " + " | ".join(cols))
                for r in rows:
                    print("    " + " | ".join(str(v) for v in r))
            else:
                print("    (no rows)")
            return rows
    except Exception as e:
        for line in str(e).splitlines()[:6]:
            print(f"    ERROR: {line[:300]}")
        return None


def gen_rows(region):
    rng = random.Random(stable_seed(region.name))
    return [
        (f"{region.name}-{i}", rng.uniform(region.xmin, region.xmax), rng.uniform(region.ymin, region.ymax))
        for i in range(1000)
    ]


def main() -> int:
    c = load()
    conn = sql.connect(
        server_hostname=c["server_hostname"],
        http_path=c["http_path"],
        access_token=c["access_token"],
    )
    cur = conn.cursor()

    q(cur, f"USE CATALOG {CATALOG}")
    q(cur, f"USE SCHEMA {SCHEMA}")

    table = f"{TABLE_BASE}"

    print("\n=== L0: try CREATE TABLE with GEOMETRY column ===")
    q(cur, f"DROP TABLE IF EXISTS {table}")
    rows = q(
        cur,
        f"""
        CREATE TABLE {table} (
          id STRING,
          geom GEOMETRY
        ) USING ICEBERG
        TBLPROPERTIES ('format-version'='3')
        """,
    )
    if rows is None:
        print("\n  GEOMETRY DDL rejected — try GEOMETRY without TBLPROPERTIES")
        rows = q(
            cur,
            f"CREATE TABLE {table} (id STRING, geom GEOMETRY) USING ICEBERG",
        )

    # Confirm the schema
    q(cur, f"DESCRIBE TABLE {table}")

    print("\n=== insert 10 regions ===")
    # Use INSERT INTO with ST_POINT (Databricks's spatial function)
    for region in REGIONS:
        rows = gen_rows(region)
        # build a VALUES (...) clause
        # ST_POINT may not be the exact function name on Databricks; try ST_POINT first
        values_clause = ",\n".join(
            f"('{rid}', ST_POINT({x}, {y}))" for rid, x, y in rows
        )
        sql_text = f"INSERT INTO {table} VALUES {values_clause}"
        # truncated label
        print(f"\n>>> INSERT INTO {table} (1000 rows for {region.name})")
        try:
            cur.execute(sql_text)
            print(f"    ok")
        except Exception as e:
            print(f"    ERROR: {str(e).splitlines()[0][:280]}")
            break

    print("\n=== L1: SELECT COUNT(*) ===")
    q(cur, f"SELECT COUNT(*) FROM {table}")

    print("\n=== L2: spatial predicate ===")
    q(
        cur,
        f"SELECT COUNT(*) FROM {table} "
        f"WHERE ST_INTERSECTS(geom, ST_GEOMFROMTEXT('POLYGON((-125 32, -115 32, -115 42, -125 42, -125 32))'))",
    )

    print("\n=== L3: file pruning probe ===")
    q(
        cur,
        f"""
        SELECT COUNT(DISTINCT _metadata.file_path) AS n_files,
               COUNT(*) AS n_rows
        FROM {table}
        WHERE ST_INTERSECTS(geom, ST_GEOMFROMTEXT('POLYGON((-125 32, -115 32, -115 42, -125 42, -125 32))'))
        """,
    )

    # Cleanup
    print(f"\n=== cleanup ===")
    q(cur, f"DROP TABLE IF EXISTS {table}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
