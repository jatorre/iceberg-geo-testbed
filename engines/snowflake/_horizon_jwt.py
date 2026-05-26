"""End-to-end Snowflake Horizon Catalog JWT auth bootstrap.

Steps:
  1. Generate (or load) an RSA keypair locally.
  2. Upload the public key to the Snowflake user via ALTER USER.
  3. Compute the SHA-256 fingerprint of the DER-encoded public key.
  4. Sign a JWT with the private key, claims per Snowflake's spec.
  5. Exchange the JWT for an OAuth access token at the Horizon
     /v1/oauth/tokens endpoint.
  6. Print the access token so it can be passed to DuckDB ATTACH.

Output prints both the bearer-style auth header to use directly and
a DuckDB-ready SECRET stanza.
"""

from __future__ import annotations

import base64
import hashlib
import sys
import time
from pathlib import Path

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt as pyjwt

sys.path.insert(0, str(Path(__file__).parent))

import snowflake.connector  # noqa: E402

from _creds import load as load_creds  # noqa: E402


# These are derived from the Snowflake account / user identifier.
ACCOUNT_LOCATOR = "KQ34251"  # confirmed via CURRENT_ACCOUNT()
USER = "JATORRETESTBED"
ROLE = "ACCOUNTADMIN"
HORIZON_BASE = "https://kjeidxa-ik05112.snowflakecomputing.com/polaris/api/catalog"

KEY_DIR = Path.home() / ".config" / "iceberg-geo-testbed" / "horizon-keys"
PRIVATE_PEM = KEY_DIR / "rsa_key.pem"
PUBLIC_PEM = KEY_DIR / "rsa_key.pub"


def ensure_keypair() -> tuple[bytes, bytes]:
    if PRIVATE_PEM.exists() and PUBLIC_PEM.exists():
        print(f"reusing existing keypair at {KEY_DIR}")
        return PRIVATE_PEM.read_bytes(), PUBLIC_PEM.read_bytes()

    print(f"generating new RSA 2048 keypair at {KEY_DIR}")
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    PRIVATE_PEM.write_bytes(private_pem)
    PRIVATE_PEM.chmod(0o600)
    PUBLIC_PEM.write_bytes(public_pem)
    PUBLIC_PEM.chmod(0o644)
    return private_pem, public_pem


def public_key_fingerprint(public_pem: bytes) -> str:
    """SHA256:<base64> fingerprint of the DER SubjectPublicKeyInfo."""
    public_key = serialization.load_pem_public_key(public_pem)
    der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashlib.sha256(der).digest()
    b64 = base64.b64encode(digest).decode("ascii")
    return f"SHA256:{b64}"


def upload_public_key(public_pem: bytes) -> None:
    """ALTER USER … SET RSA_PUBLIC_KEY = '<base64>' — using the existing
    password creds (which still work for the SQL endpoint, just not for
    the Horizon OAuth endpoint)."""
    body = public_pem.decode("ascii")
    # Snowflake wants the base64 body only — no -----BEGIN ----- markers,
    # no newlines.
    inner = (
        body.replace("-----BEGIN PUBLIC KEY-----", "")
        .replace("-----END PUBLIC KEY-----", "")
        .replace("\n", "")
        .strip()
    )
    creds = load_creds()
    conn = snowflake.connector.connect(**creds, role="ACCOUNTADMIN")
    cur = conn.cursor()
    print(f"uploading public key to user {USER}")
    cur.execute(f"ALTER USER {USER} SET RSA_PUBLIC_KEY = '{inner}'")
    rows = cur.fetchall()
    print(f"  {rows}")
    cur.close()
    conn.close()


def make_jwt(private_pem: bytes, fingerprint: str) -> str:
    """Snowflake JWT claims per docs:
        iss = <ACCOUNT>.<USER>.<fingerprint>
        sub = <ACCOUNT>.<USER>
        iat = now (seconds)
        exp = iat + <up to 1 hour>
    Algorithm: RS256.
    """
    qualified = f"{ACCOUNT_LOCATOR}.{USER}"
    now = int(time.time())
    claims = {
        "iss": f"{qualified}.{fingerprint}",
        "sub": qualified,
        "iat": now,
        "exp": now + 3600,
    }
    return pyjwt.encode(claims, private_pem, algorithm="RS256")


def exchange_for_oauth_token(jwt_token: str) -> str:
    """POST to Horizon's /v1/oauth/tokens. The Snowflake docs say to
    pass `client_secret=<JWT>` with grant_type=client_credentials
    and scope=session:role:<ROLE>."""
    url = f"{HORIZON_BASE}/v1/oauth/tokens"
    data = {
        "grant_type": "client_credentials",
        "scope": f"session:role:{ROLE}",
        "client_secret": jwt_token,
    }
    print(f"\nPOST {url}")
    resp = requests.post(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    print(f"  status: {resp.status_code}")
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    if resp.status_code != 200:
        print(f"  body: {body}")
        sys.exit(1)
    return body["access_token"]


def main() -> int:
    private_pem, public_pem = ensure_keypair()
    fp = public_key_fingerprint(public_pem)
    print(f"fingerprint: {fp}")

    upload_public_key(public_pem)

    token = make_jwt(private_pem, fp)
    print(f"\nJWT (first 60 chars): {token[:60]}…")

    access_token = exchange_for_oauth_token(token)
    print(f"\n✅ access_token: {access_token[:60]}…")
    print(f"\n=== ready for DuckDB ===")
    print(f"export HORIZON_TOKEN='{access_token}'")
    print(f"# DuckDB:")
    print(f"#   CREATE SECRET horizon (")
    print(f"#     TYPE iceberg, TOKEN '{access_token[:30]}…');")
    print(f"#   ATTACH 'TESTBED' AS sf (TYPE iceberg, SECRET horizon,")
    print(f"#     ENDPOINT '{HORIZON_BASE}');")
    # Also write the token to a file for easy reuse
    token_file = KEY_DIR / "access_token"
    token_file.write_text(access_token)
    token_file.chmod(0o600)
    print(f"\ntoken cached at {token_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
