"""Snowflake credentials loader, with two backends:

1. **File** (default if `SNOWFLAKE_CREDS_FILE` is set or the default file
   exists). A plain-text 3-line file in this format:

       https://<account>.snowflakecomputing.com/...
       <user>
       <password>

   `account` is derived from the host of the URL (the part before
   `.snowflakecomputing.com`).

   Default path: `$SNOWFLAKE_CREDS_FILE` or `~/.config/iceberg-geo-testbed/
   snowflake.txt`. This is the path we use for personal accounts where we
   have ACCOUNTADMIN.

2. **gcloud secret** — reads the CARTO `carto-dev-database-credentials`
   secret in the `cartobq` project (works when we're authed via
   `gcloud auth login`). This was the path used during the discovery phase
   on the shared CARTO dev account; that account didn't have ACCOUNTADMIN
   so it's now historical, but still useful for re-running the discovery.

Pick explicitly with `SNOWFLAKE_PROFILE=file|gcloud`. Otherwise the loader
prefers `file` if a creds file exists, falls back to `gcloud`.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_CREDS_FILE = Path.home() / ".config" / "iceberg-geo-testbed" / "snowflake.txt"

GCLOUD_SECRET_NAME = "carto-dev-database-credentials"
GCLOUD_SECRET_PROJECT = "cartobq"


def _from_file(path: Path) -> dict:
    raw = path.read_text().strip().splitlines()
    if len(raw) < 3:
        raise ValueError(
            f"{path}: expected 3 lines (URL, user, password); got {len(raw)}"
        )
    url, user, password = (line.strip() for line in raw[:3])
    host = urlparse(url).hostname or ""
    account = host.split(".")[0].upper()
    if not account:
        raise ValueError(f"{path}: could not parse account from URL {url!r}")
    return {
        "account": account,
        "user": user,
        "password": password,
    }


def _from_gcloud() -> dict:
    raw = subprocess.check_output(
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={GCLOUD_SECRET_NAME}",
            f"--project={GCLOUD_SECRET_PROJECT}",
        ]
    )
    blob = json.loads(raw)
    sf = blob["databases"]["snowflake"]["config"]["credentials"]
    return {
        "account": sf["account"],
        "user": sf["username"],
        "password": sf["password"],
        "database": sf["database"],
        "warehouse": sf["warehouse"],
        "role": sf["role"],
    }


def load() -> dict:
    profile = os.environ.get("SNOWFLAKE_PROFILE", "").lower()
    creds_file = Path(os.environ.get("SNOWFLAKE_CREDS_FILE", str(DEFAULT_CREDS_FILE)))

    if profile == "gcloud":
        return _from_gcloud()
    if profile == "file":
        return _from_file(creds_file)

    # Auto: prefer file if it exists.
    if creds_file.is_file():
        return _from_file(creds_file)
    return _from_gcloud()


if __name__ == "__main__":
    c = load()
    redacted = {**c, "password": f"<{len(c['password'])} chars>"}
    print(json.dumps(redacted, indent=2))
