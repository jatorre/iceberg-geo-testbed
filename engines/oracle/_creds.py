"""Oracle Autonomous Database credentials loader. Reads from the CARTO
`carto-dev-database-credentials` gcloud secret and assumes the wallet
files have been extracted to ~/.config/iceberg-geo-testbed/oracle-wallet/
(see `_setup_wallet.sh` or the README for the one-shot extraction).

Returns a dict suitable for `oracledb.connect(**creds)` (thin mode with
wallet).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


SECRET_NAME = "carto-dev-database-credentials"
SECRET_PROJECT = "cartobq"

DEFAULT_WALLET_DIR = Path.home() / ".config" / "iceberg-geo-testbed" / "oracle-wallet"
DEFAULT_DSN = os.environ.get("ORACLE_DSN", "acmefreetier_high")


def load() -> dict:
    raw = subprocess.check_output(
        [
            "gcloud", "secrets", "versions", "access", "latest",
            f"--secret={SECRET_NAME}", f"--project={SECRET_PROJECT}",
        ]
    )
    blob = json.loads(raw)
    o = blob["databases"]["oracle"]["config"]["credentials"]

    wallet_dir = Path(os.environ.get("ORACLE_WALLET_DIR", str(DEFAULT_WALLET_DIR)))
    if not (wallet_dir / "tnsnames.ora").exists():
        raise FileNotFoundError(
            f"Wallet not found at {wallet_dir}. See engines/oracle/README.md "
            f"for the extraction step."
        )
    return {
        "user": o["user"],
        "password": o["password"],
        "dsn": DEFAULT_DSN,
        "config_dir": str(wallet_dir),
        "wallet_location": str(wallet_dir),
        "wallet_password": o["walletPassword"],
    }


if __name__ == "__main__":
    c = load()
    print(json.dumps(
        {**c,
         "password": f"<{len(c['password'])} chars>",
         "wallet_password": f"<{len(c['wallet_password'])} chars>"},
        indent=2,
    ))
