"""One-shot: connect with the dev creds and report whether this Snowflake
account is set up to run the Iceberg V3 geometry probes.

Three checks:
  1. account/version/region
  2. roles available to the user, and whether any can CREATE EXTERNAL VOLUME
  3. existing EXTERNAL VOLUMES / CATALOG INTEGRATIONS / ICEBERG TABLES

Read-only except for one `CREATE OR REPLACE EXTERNAL VOLUME` probe against
a nonexistent bucket — this fails fast on insufficient privileges before
the storage URL is validated, so it's safe.
"""

from __future__ import annotations

import sys

import snowflake.connector

from _creds import load


def q(cur, sql: str, limit: int = 50):
    print(f"\n>>> {sql}")
    try:
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit)
        if not rows:
            print("    (no rows)")
            return
        print("    " + " | ".join(cols))
        for r in rows:
            print("    " + " | ".join(str(v) for v in r))
        if len(rows) == limit:
            print(f"    ... (truncated at {limit})")
    except Exception as e:
        print(f"    ERROR: {type(e).__name__}: {e}")


def main() -> int:
    creds = load()
    conn = snowflake.connector.connect(**creds)
    cur = conn.cursor()

    print("=== account ===")
    q(cur, "SELECT CURRENT_VERSION(), CURRENT_REGION(), CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE()")

    print("\n=== roles available to user ===")
    q(cur, "SELECT CURRENT_AVAILABLE_ROLES()")

    print("\n=== existing iceberg-related infra ===")
    q(cur, "SHOW EXTERNAL VOLUMES")
    q(cur, "SHOW CATALOG INTEGRATIONS")
    q(cur, "SHOW STORAGE INTEGRATIONS")

    print("\n=== privilege probe: can current role create an external volume? ===")
    # A bucket that doesn't exist — Snowflake checks the role privilege before
    # the URL, so this fails fast with 'Insufficient privileges' if so.
    q(
        cur,
        "CREATE OR REPLACE EXTERNAL VOLUME _ICEBERG_TESTBED_PROBE "
        "STORAGE_LOCATIONS = ("
        "  ("
        "    NAME='probe', "
        "    STORAGE_PROVIDER='S3', "
        "    STORAGE_BASE_URL='s3://snowflake-probe-bucket-does-not-exist/', "
        "    STORAGE_AWS_ROLE_ARN='arn:aws:iam::000000000000:role/dummy'"
        "  )"
        ")",
    )

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
