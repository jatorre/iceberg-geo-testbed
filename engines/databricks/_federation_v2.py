"""Databricks ↔ Snowflake federation against a Snowflake-managed V2
GeoIceberg table (bbox doubles + geom_wkb BINARY).

Two stages (see engines/databricks/README.md, 2026-05-26 update):

  Stage 1 — QUERY FEDERATION (this script reproduces it):
    CREATE CONNECTION TYPE snowflake + CREATE FOREIGN CATALOG, then read
    the Snowflake-managed V2 table. Databricks pulls rows via JDBC and
    runs st_geomfromwkb()/st_intersects() locally. Proves V2 + WKB is
    portable into Databricks today. Verified counts match Snowflake:
    COUNT=10000, bbox=196, polygon point-in-poly=1000.

  Stage 2 — CATALOG FEDERATION (direct-from-GCS Iceberg read): does NOT
    engage for Snowflake-on-GCP. Even with a working read-only GCS
    storage credential + external location, EXPLAIN shows SnowflakePlan
    (JDBC), because Databricks's direct-read path only accepts the `gs://`
    scheme while Snowflake-on-GCP vends its metadata location as `gcs://`.
    Databricks explicitly rejects `gcs://` for external locations
    ("invalid URI scheme gcs. Valid URI schemes include … gs …"), so the
    metadata location can't be matched to a governed location and it falls
    back to JDBC. GCP-specific; on AWS (s3://) / Azure (abfss://) it would
    line up. Stage 2 was a one-off manual test (dedicated read-only SA →
    SA-key storage credential via the UC REST API → external location);
    not automated here because it exports a GCP credential into the
    sandbox.

Prereqs:
  - Sandbox creds (3 lines: host / http_path / token) at
    ~/.config/iceberg-geo-testbed/databricks-sandbox.txt — a Databricks
    Free Edition workspace where you're metastore admin (needs
    CREATE CONNECTION).
  - Snowflake creds via engines/snowflake/_creds.py, and the managed V2
    table built (python engines/snowflake/_managed_v2_test.py).

Run: python engines/databricks/_federation_v2.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "engines" / "snowflake"))

from databricks import sql

from _creds import load as load_sf  # noqa: E402  (snowflake _creds on path)

SANDBOX_CREDS = Path.home() / ".config" / "iceberg-geo-testbed" / "databricks-sandbox.txt"
SF_HOST = "kjeidxa-ik05112.snowflakecomputing.com"
CONN = "sf_testbed"
CATALOG = "sf_testbed"
TABLE = f"{CATALOG}.public2.managed_v2_geo"


def _sandbox():
    host, http_path, token = SANDBOX_CREDS.read_text().strip().splitlines()[:3]
    return host.strip(), http_path.strip(), token.strip()


def run(cur, label, stmt, show=True):
    print(f"\n>>> {label}")
    try:
        cur.execute(stmt)
        rows = cur.fetchall() if cur.description else []
        if show:
            for r in rows[:10]:
                print("   ", tuple(str(v)[:70] for v in r))
            if not rows:
                print("    (ok)")
        return rows
    except Exception as e:
        print("    ERROR:", str(e).splitlines()[0][:240])
        return None


def main() -> int:
    sf = load_sf()
    host, http_path, token = _sandbox()
    conn = sql.connect(server_hostname=host, http_path=http_path, access_token=token)
    cur = conn.cursor()

    # Stage 1: connection + foreign catalog (idempotent).
    ddl = (
        f"CREATE CONNECTION IF NOT EXISTS {CONN} TYPE snowflake OPTIONS ("
        f"host '{SF_HOST}', port '443', sfWarehouse 'COMPUTE_WH', "
        f"user '{sf['user']}', password '{sf['password']}')"
    )
    run(cur, "CREATE CONNECTION (TYPE snowflake)", ddl, show=False)
    run(
        cur,
        "CREATE FOREIGN CATALOG",
        f"CREATE FOREIGN CATALOG IF NOT EXISTS {CATALOG} "
        f"USING CONNECTION {CONN} OPTIONS (database 'TESTBED')",
        show=False,
    )

    # Probe.
    run(cur, "DESCRIBE", f"DESCRIBE {TABLE}")
    run(cur, "COUNT(*) [expect 10000]", f"SELECT COUNT(*) FROM {TABLE}")
    run(
        cur,
        "bbox predicate [expect 196]",
        f"SELECT COUNT(*) FROM {TABLE} "
        f"WHERE fp_xmin <= -118 AND fp_xmax >= -125 "
        f"AND fp_ymin <= 40 AND fp_ymax >= 37",
    )
    run(
        cur,
        "WKB parse [POINTs]",
        f"SELECT id, st_astext(st_geomfromwkb(geom_wkb)) FROM {TABLE} LIMIT 3",
    )
    run(
        cur,
        "spatial predicate via WKB [expect 1000]",
        f"SELECT COUNT(*) FROM {TABLE} WHERE st_intersects("
        f"st_geomfromwkb(geom_wkb), "
        f"st_geomfromtext('POLYGON((-125 32,-115 32,-115 42,-125 42,-125 32))'))",
    )

    # Stage 2 signal: EXPLAIN shows the read path. SnowflakePlan = JDBC.
    print("\n>>> EXPLAIN read path (SnowflakePlan = JDBC pushdown, not direct GCS)")
    try:
        cur.execute(f"EXPLAIN FORMATTED SELECT * FROM {TABLE} LIMIT 50")
        for ln in "\n".join(str(r[0]) for r in cur.fetchall()).splitlines():
            if any(k in ln.lower() for k in ("snowflakeplan", "gs://", "iceberg", "filescan")):
                print("   ", ln.strip()[:140])
    except Exception as e:
        print("    ERROR:", str(e).splitlines()[0][:200])

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
