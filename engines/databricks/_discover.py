"""One-shot Databricks discovery: who am I, what catalogs/schemas are
visible, what storage credentials / external locations exist for GCS
access, what V3 / Iceberg features are surfaced.

Read-only.
"""

from __future__ import annotations

import sys

from databricks import sql

from _creds import load


def q(cur, sql_text: str, limit: int = 40):
    print(f"\n>>> {sql_text}")
    try:
        cur.execute(sql_text)
        rows = cur.fetchmany(limit)
        if not rows:
            print("    (no rows)")
            return
        cols = [d[0] for d in cur.description] if cur.description else []
        print("    " + " | ".join(cols))
        for r in rows:
            print("    " + " | ".join(str(v) for v in r))
        if len(rows) == limit:
            print(f"    ... (truncated at {limit})")
    except Exception as e:
        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
        print(f"    ERROR: {msg[:240]}")


def main() -> int:
    c = load()
    conn = sql.connect(
        server_hostname=c["server_hostname"],
        http_path=c["http_path"],
        access_token=c["access_token"],
    )
    cur = conn.cursor()

    print("=== identity / environment ===")
    q(cur, "SELECT current_user(), current_catalog(), current_database()")
    q(cur, "SELECT current_version()")
    q(cur, "SHOW CATALOGS")

    print("\n=== current catalog: schemas + iceberg table inventory ===")
    if c.get("catalog"):
        q(cur, f"SHOW SCHEMAS IN `{c['catalog']}`")

    print("\n=== external storage / Unity Catalog primitives ===")
    q(cur, "SHOW STORAGE CREDENTIALS")
    q(cur, "SHOW EXTERNAL LOCATIONS")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
