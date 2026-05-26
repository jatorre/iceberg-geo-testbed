"""Stage all three fixtures to GCS for BigQuery.

For each fixture:
  1. Re-run the testbed build() with location_uri='gs://<bucket>/<name>' so
     the manifest avro + metadata.json reference gs:// paths.
  2. Write the new metadata files into a sibling `metadata-gcs/` dir locally
     (so the file:// `metadata/` dir DuckDB depends on is preserved).
  3. gsutil cp the data parquets + metadata-gcs to GCS at:
       gs://<bucket>/<name>/data/
       gs://<bucket>/<name>/metadata/

Configure with env vars (or edit BUCKET below):
  BUCKET — GCS bucket name. Default: cartobq-iceberg-geo-testbed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Make `testbed` importable when running this file directly.
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from testbed import v2_flat_columns, v2_bbox_struct, v3_geometry, v2_geo_convention, v3_geometry_lineage  # noqa: E402


BUCKET = os.environ.get("BUCKET", "cartobq-iceberg-geo-testbed")
META_DIR_NAME = "metadata-gcs"

FIXTURES = [
    ("v2_flat_columns", v2_flat_columns),
    ("v2_bbox_struct", v2_bbox_struct),
    ("v3_geometry", v3_geometry),
    ("v2_geo_convention", v2_geo_convention),
    ("v3_geometry_lineage", v3_geometry_lineage),
]


def main() -> int:
    for name, mod in FIXTURES:
        location_uri = f"gs://{BUCKET}/{name}"
        print(f"\n=== {name} -> {location_uri} ===")

        # Rebuild metadata with gs:// URIs. Parquet data files are also
        # rewritten by the build (deterministic; same seed) — harmless.
        local_meta = mod.build(location_uri=location_uri, meta_dir_name=META_DIR_NAME)
        print(f"  built local: {local_meta}")

        # Upload data/ and metadata-gcs/ -> gs://bucket/<name>/{data,metadata}/
        local_root = REPO / "data" / name
        data_src = local_root / "data"
        meta_src = local_root / META_DIR_NAME

        for src, remote_subdir in [(data_src, "data"), (meta_src, "metadata")]:
            remote = f"gs://{BUCKET}/{name}/{remote_subdir}/"
            print(f"  upload {src} -> {remote}")
            subprocess.run(
                ["gsutil", "-m", "rsync", "-d", "-r", str(src), remote],
                check=True,
            )

    print("\nDone. To inspect:")
    print(f"  gsutil ls -r gs://{BUCKET}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
