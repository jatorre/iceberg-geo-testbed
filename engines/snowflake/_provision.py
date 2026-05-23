"""One-shot provisioning for the Snowflake side:

  - drops the throwaway probe external volume from _discover.py if present
  - creates ICEBERG_TESTBED_VOLUME pointing at the public GCS bucket
  - dumps DESC EXTERNAL VOLUME so we can see the GCS service account
    Snowflake will use (read-only for us; the bucket is already public)
  - creates the ICEBERG_TESTBED schema + database

Idempotent — safe to re-run.
"""

from __future__ import annotations

import sys

import snowflake.connector

from _creds import load


BUCKET = "cartobq-iceberg-geo-testbed"
DB = "ICEBERG_TESTBED"
SCHEMA = "PUBLIC"
EXTERNAL_VOLUME = "ICEBERG_TESTBED_VOLUME"


def q(cur, sql: str, ignore_errors: bool = False, limit: int = 200):
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
        if ignore_errors:
            print(f"    (ignored): {e}")
        else:
            raise


def main() -> int:
    creds = load()
    # Force ACCOUNTADMIN since most DDL here requires it.
    conn = snowflake.connector.connect(**creds, role="ACCOUNTADMIN")
    cur = conn.cursor()

    q(cur, "DROP EXTERNAL VOLUME IF EXISTS _ICEBERG_TESTBED_PROBE")

    # Catalog integration for unmanaged Iceberg tables reading a static
    # metadata.json sitting in blob storage. "ICEBERG_FILES" is the
    # convention; the actual `CATALOG` clause on CREATE ICEBERG TABLE refers
    # to this integration's name.
    q(
        cur,
        """CREATE OR REPLACE CATALOG INTEGRATION ICEBERG_FILES
            CATALOG_SOURCE = OBJECT_STORE
            TABLE_FORMAT = ICEBERG
            ENABLED = TRUE""",
    )

    # Create the external volume backed by the public GCS bucket. ALLOW_WRITES
    # is FALSE because we never write — Snowflake only reads our metadata.
    q(
        cur,
        f"""CREATE OR REPLACE EXTERNAL VOLUME {EXTERNAL_VOLUME}
            STORAGE_LOCATIONS = (
              (
                NAME = 'gcs-testbed',
                STORAGE_PROVIDER = 'GCS',
                STORAGE_BASE_URL = 'gcs://{BUCKET}/'
              )
            )
            ALLOW_WRITES = FALSE""",
    )

    # Snowflake on GCP creates a per-account GCP service account during the
    # first DESC EXTERNAL VOLUME. The principal is in the descriptor.
    q(cur, f"DESC EXTERNAL VOLUME {EXTERNAL_VOLUME}")

    # Database + schema to hold the iceberg tables. Idempotent.
    q(cur, f"CREATE DATABASE IF NOT EXISTS {DB}")
    q(cur, f"USE DATABASE {DB}")
    q(cur, f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    q(cur, f"USE SCHEMA {SCHEMA}")

    cur.close()
    conn.close()
    print("\nProvisioning complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
