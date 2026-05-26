"""Probe Oracle Autonomous Database's Iceberg reader against a
metrics-enhanced V2 fixture.

Background: Oracle ADB previously rejected our pyiceberg-emitted metadata
with `ORA-20000: Iceberg parameter error / Failed to generate column
list`. The hypothesis (engines/oracle/README.md) was that Oracle treats
the spec-OPTIONAL manifest metrics (`column_sizes`, `value_counts`,
`null_value_counts`) as required to build its column list. We now emit
those (see testbed/_static_catalog.py + testbed/common.py:parquet_metrics)
and staged a fixture with them at:
  https://storage.googleapis.com/cartobq-iceberg-geo-testbed/oracle_probe/

This script registers that table and queries it. If the column-list error
is gone, the hypothesis is confirmed.

Prereqs: wallet extracted to ~/.config/iceberg-geo-testbed/oracle-wallet/
(see README), oracledb installed, gcloud auth for the creds secret.
"""

from __future__ import annotations

import sys
from pathlib import Path

import oracledb

sys.path.insert(0, str(Path(__file__).parent))
from _creds import load  # noqa: E402

BUCKET = "cartobq-iceberg-geo-testbed"
PROBE_NAME = "oracle_probe"
META_URL = f"https://storage.googleapis.com/{BUCKET}/{PROBE_NAME}/metadata/v1.metadata.json"
TABLE = "ORACLE_PROBE"
HOST_ACE = "storage.googleapis.com"


def run(cur, label: str, sql: str, params=None, fetch: bool = True):
    print(f"\n>>> {label}")
    try:
        cur.execute(sql, params or {})
        if fetch and cur.description:
            rows = cur.fetchall()
            for r in rows[:10]:
                print("   ", tuple(str(v)[:60] for v in r))
            if not rows:
                print("    (no rows)")
            return rows
        print("    OK")
        return None
    except Exception as e:
        print("    ERROR:", str(e).splitlines()[0][:260])
        return False


def main() -> int:
    creds = load()
    conn = oracledb.connect(**creds)
    cur = conn.cursor()

    # Network ACL for outbound HTTPS to GCS (idempotent; may already exist).
    run(
        cur,
        "ensure network ACL for GCS",
        """BEGIN
             DBMS_NETWORK_ACL_ADMIN.APPEND_HOST_ACE(
               host => :h,
               ace  => xs$ace_type(privilege_list => xs$name_list('http'),
                                   principal_name => :u,
                                   principal_type => xs_acl.ptype_db));
           EXCEPTION WHEN OTHERS THEN NULL;
           END;""",
        {"h": HOST_ACE, "u": creds["user"].upper()},
        fetch=False,
    )

    # Drop any prior probe table.
    run(cur, "drop prior table", f"BEGIN EXECUTE IMMEDIATE 'DROP TABLE {TABLE}'; "
        f"EXCEPTION WHEN OTHERS THEN NULL; END;", fetch=False)

    # Register the Iceberg table by metadata.json URL.
    run(
        cur,
        "CREATE_EXTERNAL_TABLE (iceberg)",
        """BEGIN
             DBMS_CLOUD.CREATE_EXTERNAL_TABLE(
               table_name    => :t,
               file_uri_list => :u,
               format        => '{"access_protocol":{"protocol_type":"iceberg"}}');
           END;""",
        {"t": TABLE, "u": META_URL},
        fetch=False,
    )

    # If registration worked, query it.
    run(cur, "COUNT(*) [expect 10000]", f"SELECT COUNT(*) FROM {TABLE}")
    run(
        cur,
        "bbox predicate [expect 196]",
        f"""SELECT COUNT(*) FROM {TABLE}
            WHERE xmin <= -118 AND xmax >= -125
              AND ymin <= 40 AND ymax >= 37""",
    )

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
