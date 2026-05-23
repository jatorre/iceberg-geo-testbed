"""Databricks REST credentials loader. Reads from the CARTO
`carto-dev-database-credentials` gcloud secret (the same one the other
engine runners use) or from local env vars.

Env-var override (skips gcloud secret round-trip):
  DATABRICKS_HOST, DATABRICKS_TOKEN, DATABRICKS_HTTP_PATH
"""

from __future__ import annotations

import json
import os
import subprocess


SECRET_NAME = "carto-dev-database-credentials"
SECRET_PROJECT = "cartobq"


def _from_env() -> dict | None:
    host = os.environ.get("DATABRICKS_HOST")
    token = os.environ.get("DATABRICKS_TOKEN")
    http_path = os.environ.get("DATABRICKS_HTTP_PATH")
    if not (host and token and http_path):
        return None
    return {
        "server_hostname": host,
        "access_token": token,
        "http_path": http_path,
        "catalog": os.environ.get("DATABRICKS_CATALOG"),
    }


def _from_gcloud() -> dict:
    raw = subprocess.check_output(
        [
            "gcloud", "secrets", "versions", "access", "latest",
            f"--secret={SECRET_NAME}", f"--project={SECRET_PROJECT}",
        ]
    )
    blob = json.loads(raw)
    db = blob["databases"]["databricks"]["config"]["credentials"]
    return {
        "server_hostname": db["host"],
        "access_token": db["token"],
        "http_path": f"/sql/1.0/warehouses/{db['warehouseId']}",
        "catalog": db.get("catalog"),
    }


def load() -> dict:
    return _from_env() or _from_gcloud()


if __name__ == "__main__":
    c = load()
    print(json.dumps(
        {**c, "access_token": f"<{len(c['access_token'])} chars>"},
        indent=2,
    ))
