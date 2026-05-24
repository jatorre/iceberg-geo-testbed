"""Register our three fixtures with the Polaris REST catalog running on
the GCE VM.

Polaris exposes the Iceberg REST catalog API at:
  http://<host>:8181/api/catalog/v1/...

Auth: OAuth2 client-credentials against realm POLARIS with the bootstrap
client_id/client_secret we set in the startup script (root/s3cr3t).

Flow:
  1. POST /api/catalog/v1/oauth/tokens → access_token
  2. POST /api/management/v1/catalogs → create a catalog pointing at GCS
  3. PUT  /api/catalog/v1/{cat}/namespaces → create per-fixture namespaces
  4. POST /api/catalog/v1/{cat}/namespaces/{ns}/register → register existing
     table by metadata_location
"""

from __future__ import annotations

import json
import os
import sys

import urllib.request
import urllib.error


HOST = os.environ.get("POLARIS_HOST", "136.112.253.147")
PORT = 8181
BASE = f"http://{HOST}:{PORT}"

REALM = "POLARIS"
CLIENT_ID = "root"
CLIENT_SECRET = "s3cr3t"

CATALOG = "testbed"
BUCKET = "cartobq-iceberg-geo-testbed"
DEFAULT_BASE_LOCATION = f"gs://{BUCKET}"

FIXTURES = ["v2_flat_columns", "v2_bbox_struct", "v3_geometry"]


def http(method: str, path: str, *, token: str | None = None, json_body=None, form_body=None):
    url = f"{BASE}{path}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif form_body is not None:
        body = urllib.parse.urlencode(form_body).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            return resp.status, json.loads(data) if data else None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body


def get_token() -> str:
    import urllib.parse  # noqa: F401  (imported inside http() too)
    code, body = http(
        "POST",
        "/api/catalog/v1/oauth/tokens",
        form_body={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "PRINCIPAL_ROLE:ALL",
        },
    )
    if code >= 300:
        raise RuntimeError(f"OAuth failed: {code} {body}")
    return body["access_token"]


def ensure_catalog(token: str) -> None:
    # Check first
    code, body = http("GET", f"/api/management/v1/catalogs/{CATALOG}", token=token)
    if code < 300:
        print(f"catalog {CATALOG} already exists")
        return
    code, body = http(
        "POST",
        "/api/management/v1/catalogs",
        token=token,
        json_body={
            "name": CATALOG,
            "type": "INTERNAL",
            "properties": {
                "default-base-location": DEFAULT_BASE_LOCATION,
            },
            "storageConfigInfo": {
                "storageType": "GCS",
                "allowedLocations": [DEFAULT_BASE_LOCATION + "/"],
            },
        },
    )
    print(f"create catalog → {code}: {body}")


def ensure_namespace(token: str, ns: str) -> None:
    code, body = http(
        "POST",
        f"/api/catalog/v1/{CATALOG}/namespaces",
        token=token,
        json_body={"namespace": [ns]},
    )
    print(f"  create namespace {ns} → {code}")


def register_table(token: str, ns: str, name: str) -> None:
    metadata_uri = f"gs://{BUCKET}/{ns}/metadata/v1.metadata.json"
    code, body = http(
        "POST",
        f"/api/catalog/v1/{CATALOG}/namespaces/{ns}/register",
        token=token,
        json_body={
            "name": name,
            "metadata-location": metadata_uri,
        },
    )
    print(f"  register table {ns}.{name} → {code}")
    if code >= 300:
        print(f"    body: {body}")


def main() -> int:
    print(f"Polaris @ {BASE}")
    print("getting token…")
    token = get_token()
    print(f"  token length: {len(token)}")

    print("\nensuring catalog…")
    ensure_catalog(token)

    print("\nregistering fixtures…")
    for fx in FIXTURES:
        ensure_namespace(token, fx)
        register_table(token, fx, fx)

    print("\nlist tables")
    for fx in FIXTURES:
        code, body = http("GET", f"/api/catalog/v1/{CATALOG}/namespaces/{fx}/tables", token=token)
        print(f"  {fx}: {code} {body}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
