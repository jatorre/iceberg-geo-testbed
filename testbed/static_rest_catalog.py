"""Generate a fully **static** Iceberg REST Catalog (IRC) surface over the
testbed fixtures — the "Portolan" serverless-catalog pattern.

A normal IRC catalog is a running server. This instead pre-renders the IRC
read endpoints as plain JSON objects on a bucket, so a *generic* IRC client
(DuckDB `ATTACH`, AWS Glue federation, Spark, Trino) can consume our catalog
with no server at all. The layout mirrors the prototype proven in the
portolan `add-sdi-experiment` branch (verified working with DuckDB `ATTACH`).

Endpoints, as bare object keys under the catalog base
(`https://storage.googleapis.com/<bucket>/<CATALOG_PREFIX>`):

    v1/config
    v1/{prefix}/namespaces
    v1/{prefix}/namespaces/{ns}
    v1/{prefix}/namespaces/{ns}/tables
    v1/{prefix}/namespaces/{ns}/tables/{table}   (loadTable: metadata inline)

We upload directly to the bare keys — object stores are flat key-spaces, so
the file-vs-directory collision that forces the `__list__`/`__detail__`
convention on a local filesystem never arises here.

The data layer (`<CATALOG_PREFIX>/data/{ns}/{table}/...`) is produced by the
existing fixture builders; the loadTable response embeds each table's metadata
inline and points `metadata-location` at it.

Build + inspect locally (no cloud):
    python -m testbed.static_rest_catalog            # stages to ./data/_catalog
Publish (needs gcloud auth):
    python -m testbed.static_rest_catalog --publish
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import (
    v2_bbox_struct,
    v2_flat_columns,
    v2_geo_convention,
    v3_geometry,
    v3_geometry_lineage,
)

BUCKET = "cartobq-iceberg-geo-testbed"
CATALOG_PREFIX = "catalog"  # everything lives under gs://<bucket>/catalog/
IRC_PREFIX = "geo"  # the {prefix} path segment in /v1/{prefix}/...
BASE_URI = f"https://storage.googleapis.com/{BUCKET}/{CATALOG_PREFIX}"

# namespace -> list of (table_name, fixture_module)
NAMESPACES: dict[str, list] = {
    "v2": [
        ("v2_flat_columns", v2_flat_columns),
        ("v2_bbox_struct", v2_bbox_struct),
        ("v2_geo_convention", v2_geo_convention),
    ],
    "v3": [
        ("v3_geometry", v3_geometry),
        ("v3_geometry_lineage", v3_geometry_lineage),
    ],
}

REPO = Path(__file__).resolve().parent.parent
STAGING = REPO / "data" / "_catalog"  # local staging mirror of the bucket layout


# --- IRC response shapes (mirror the Iceberg REST OpenAPI spec) -------------


def config_response() -> dict:
    return {
        "defaults": {},
        "overrides": {"prefix": IRC_PREFIX},
        "endpoints": [
            f"GET /v1/{IRC_PREFIX}/namespaces",
            f"GET /v1/{IRC_PREFIX}/namespaces/{{namespace}}",
            f"GET /v1/{IRC_PREFIX}/namespaces/{{namespace}}/tables",
            f"GET /v1/{IRC_PREFIX}/namespaces/{{namespace}}/tables/{{table}}",
        ],
    }


def namespaces_list(namespaces: list[str]) -> dict:
    return {"namespaces": [[ns] for ns in namespaces]}


def namespace_detail(namespace: str) -> dict:
    return {"namespace": [namespace], "properties": {}}


def tables_list(namespace: str, table_names: list[str]) -> dict:
    return {"identifiers": [{"namespace": [namespace], "name": t} for t in table_names]}


def load_table_response(metadata: dict, metadata_location: str) -> dict:
    return {"metadata-location": metadata_location, "metadata": metadata, "config": {}}


# --- build the data layer (fixtures) + collect their metadata ---------------


def build_data_layer() -> dict[str, dict]:
    """Build each fixture with the catalog data location, copy its local
    parquet + metadata into the staging tree under
    `data/{ns}/{table}/`, and return {(<ns>/<table>): metadata_dict}.
    """
    out: dict[str, dict] = {}
    for ns, tables in NAMESPACES.items():
        for table_name, mod in tables:
            data_path = f"data/{ns}/{table_name}"
            location_uri = f"{BASE_URI}/{data_path}"
            # Clean any prior metadata-irc so we emit exactly one fresh
            # snapshot (build() appends snapshots; stale manifests would
            # otherwise pile up in the published reference catalog).
            stale = REPO / "data" / table_name / "metadata-irc"
            if stale.exists():
                shutil.rmtree(stale)
            # Build with the catalog URL baked into the metadata + manifests.
            meta_path = mod.build(location_uri=location_uri, meta_dir_name="metadata-irc")
            metadata = json.loads(Path(meta_path).read_text())

            # Mirror the fixture's local data/ + metadata-irc/ into staging.
            fixture_root = REPO / "data" / table_name
            dst = STAGING / data_path
            (dst / "data").mkdir(parents=True, exist_ok=True)
            (dst / "metadata").mkdir(parents=True, exist_ok=True)
            for p in (fixture_root / "data").glob("*.parquet"):
                shutil.copy2(p, dst / "data" / p.name)
            for p in (fixture_root / "metadata-irc").glob("*"):
                if p.is_file():
                    shutil.copy2(p, dst / "metadata" / p.name)
            out[f"{ns}/{table_name}"] = metadata
    return out


# --- assemble the v1/ surface as {object_key: json_text} --------------------


def assemble_surface(metadata_by_path: dict[str, dict]) -> dict[str, str]:
    surface: dict[str, str] = {}

    def put(key: str, body: dict):
        surface[key] = json.dumps(body, indent=2)

    put("v1/config", config_response())
    put(f"v1/{IRC_PREFIX}/namespaces", namespaces_list(list(NAMESPACES.keys())))

    for ns, tables in NAMESPACES.items():
        put(f"v1/{IRC_PREFIX}/namespaces/{ns}", namespace_detail(ns))
        names = [t for t, _ in tables]
        put(f"v1/{IRC_PREFIX}/namespaces/{ns}/tables", tables_list(ns, names))
        for table_name, _ in tables:
            metadata = metadata_by_path[f"{ns}/{table_name}"]
            metadata_location = f"{BASE_URI}/data/{ns}/{table_name}/metadata/v1.metadata.json"
            put(
                f"v1/{IRC_PREFIX}/namespaces/{ns}/tables/{table_name}",
                load_table_response(metadata, metadata_location),
            )
    return surface


def _safe_local_path(key: str) -> str:
    """Map a bare REST key to a collision-free local path (the
    `__list__`/`__detail__` convention — a local FS can't have a file and a
    directory at the same path; object stores can)."""
    if key.endswith("/namespaces"):  # namespaces list
        return key + "/__list__"
    if key.endswith("/tables"):  # tables list (sibling of the tables/ dir)
        return key + "__list__"
    parts = key.split("/")
    if len(parts) >= 2 and parts[-2] == "namespaces":  # namespace detail
        return key + "/__detail__"
    return key  # config + per-table leaves


def write_surface_local(surface: dict[str, str]) -> None:
    for key, text in surface.items():
        dest = STAGING / _safe_local_path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)


# --- publish to GCS (bare keys, application/json) ---------------------------


def publish(surface: dict[str, str]) -> None:
    import tempfile

    # Surface objects: upload each in-memory body to its BARE REST key with a
    # JSON content-type (keys are extension-less REST paths).
    with tempfile.TemporaryDirectory() as tmp:
        for key in sorted(surface):
            tmpf = Path(tmp) / "obj.json"
            tmpf.write_text(surface[key])
            remote = f"gs://{BUCKET}/{CATALOG_PREFIX}/{key}"
            subprocess.run(
                ["gsutil", "-h", "Content-Type:application/json", "cp", str(tmpf), remote],
                check=True,
            )
    # Data layer: rsync the whole data/ tree.
    src = STAGING / "data"
    remote = f"gs://{BUCKET}/{CATALOG_PREFIX}/data/"
    subprocess.run(["gsutil", "-m", "rsync", "-r", str(src), remote], check=True)
    print(f"\nPublished. IRC base URI: {BASE_URI}")
    print(f"  config: {BASE_URI}/v1/config")


def main() -> int:
    if STAGING.exists():
        shutil.rmtree(STAGING)
    print("Building data layer (fixtures with catalog URLs)…")
    metadata_by_path = build_data_layer()
    print("Assembling v1/ IRC surface…")
    surface = assemble_surface(metadata_by_path)
    write_surface_local(surface)

    print(f"\nStaged {len(surface)} surface objects under {STAGING}/")
    for key in sorted(surface):
        print(f"  {key}")

    if "--publish" in sys.argv:
        print("\nPublishing to GCS…")
        publish(surface)
    else:
        print(f"\n(dry run — re-run with --publish to upload. Base URI: {BASE_URI})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
