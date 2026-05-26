"""Shared "static catalog" builder. Writes the manifest avro, manifest-list
avro, and metadata.json for an Iceberg V2 (or V2-with-V3-claimed-metadata)
table sitting next to a set of parquet data files on disk.

This is the "Portolan lightweight Iceberg" pattern: no live catalog server,
just static files. Consumers point `iceberg_scan(...)` at the metadata.json.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from pyiceberg.io.pyarrow import PyArrowFileIO
from pyiceberg.manifest import (
    DataFile,
    DataFileContent,
    FileFormat,
    ManifestEntry,
    ManifestEntryStatus,
    write_manifest,
    write_manifest_list,
)
from pyiceberg.partitioning import PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.typedef import Record


def write_static_catalog(
    *,
    table_root: Path,
    iceberg_schema: Schema,
    schema_json_fields: list[dict],
    name_mapping: list[dict],
    data_files: list[dict],
    format_version_in_metadata: int = 2,
    location_uri: str | None = None,
    meta_dir_name: str = "metadata",
    extra_properties: dict[str, str] | None = None,
) -> Path:
    """Write metadata.json + manifest + manifest-list for a table.

    Args:
      table_root: filesystem directory containing `data/` (parquets) and where
        `metadata/` will be created. Files are always written to this local
        path; `location_uri` only controls the URIs recorded inside the
        metadata for engines that read it from a different location later.
      iceberg_schema: pyiceberg Schema used by the manifest writer. Field IDs
        must match the schema JSON.
      schema_json_fields: the `fields` array as it should appear in metadata.json.
        Lets callers use V3 types (e.g. `"geometry(OGC:CRS84)"`) that pyiceberg
        can't yet represent in its Python schema model.
      name_mapping: list for `schema.name-mapping.default` table property.
      data_files: each dict has keys
        - "path": relative-to-table-root path (e.g. "data/california.parquet")
        - "size": file size in bytes
        - "rows": record count
        - "lower": dict[field_id -> bytes] for per-file lower bounds (per the
          binding column type's bound encoding)
        - "upper": dict[field_id -> bytes] for per-file upper bounds
      format_version_in_metadata: write `format-version: N` to metadata.json.
        Set to 3 to claim a V3 table even though the manifest avro is V2 (until
        pyiceberg supports V3 writes natively).
      location_uri: URI prefix to record in metadata.json's `location` and in
        each data file's `file_path` (e.g. `gs://bucket/v3_geometry`). Pass
        without a trailing slash. If None (default), uses
        `file://{table_root.resolve()}` so the catalog is self-contained on
        the local disk.
      meta_dir_name: subdirectory of `table_root` to write the metadata files
        into (default `"metadata"`). When building a cloud-bound catalog
        alongside the local one, pass e.g. `"metadata-gcs"` so the file://
        catalog at `metadata/` isn't overwritten. The URI structure inside
        metadata.json always uses `<location_uri>/metadata/<file>` regardless
        — the caller's upload step is what maps the local sibling dir to
        `metadata/` on the remote.

    Returns:
      Path to the metadata.json file.
    """
    if location_uri is None:
        location_uri = f"file://{table_root.resolve()}"
    location_uri = location_uri.rstrip("/")
    meta_dir = table_root / meta_dir_name
    meta_dir.mkdir(parents=True, exist_ok=True)

    snapshot_id = int(time.time() * 1000)
    sequence_number = 1
    io = PyArrowFileIO()

    manifest_path = meta_dir / f"snap-{snapshot_id}-manifest.avro"
    with write_manifest(
        format_version=2,  # pyiceberg 0.11.1 only writes V2 manifests
        spec=PartitionSpec(),
        schema=iceberg_schema,
        output_file=io.new_output(str(manifest_path)),
        snapshot_id=snapshot_id,
        avro_compression="null",
    ) as mw:
        for df in data_files:
            mw.add_entry(
                ManifestEntry.from_args(
                    status=ManifestEntryStatus.ADDED,
                    snapshot_id=snapshot_id,
                    sequence_number=sequence_number,
                    file_sequence_number=sequence_number,
                    data_file=DataFile.from_args(
                        content=DataFileContent.DATA,
                        file_path=f"{location_uri}/{df['path']}",
                        file_format=FileFormat.PARQUET,
                        partition=Record(),
                        record_count=df["rows"],
                        file_size_in_bytes=df["size"],
                        lower_bounds=df["lower"],
                        upper_bounds=df["upper"],
                    ),
                )
            )
    manifest = mw.to_manifest_file()
    # The manifest avro is written to the local meta_dir; in the manifest-list
    # we record it under `location_uri/metadata/...` so an engine pointing at
    # the remote copy of the catalog can resolve it. ManifestFile.manifest_path
    # has no setter, but ManifestFile is a Record backed by a mutable list at
    # _data — index 0 is manifest_path.
    manifest._data[0] = f"{location_uri}/metadata/{manifest_path.name}"

    manifest_list_path = meta_dir / f"snap-{snapshot_id}-manifest-list.avro"
    with write_manifest_list(
        format_version=2,
        output_file=io.new_output(str(manifest_list_path)),
        snapshot_id=snapshot_id,
        parent_snapshot_id=None,
        sequence_number=sequence_number,
        avro_compression="null",
    ) as mlw:
        mlw.add_manifests([manifest])

    metadata = {
        "format-version": format_version_in_metadata,
        "table-uuid": str(uuid.uuid4()),
        "location": location_uri,
        "last-sequence-number": sequence_number,
        "last-updated-ms": snapshot_id,
        "last-column-id": max(f["id"] for f in schema_json_fields if "id" in f),
        "current-schema-id": 0,
        "schemas": [{"schema-id": 0, "type": "struct", "fields": schema_json_fields}],
        "default-spec-id": 0,
        "partition-specs": [{"spec-id": 0, "fields": []}],
        "last-partition-id": 999,
        "default-sort-order-id": 0,
        "sort-orders": [{"order-id": 0, "fields": []}],
        "properties": {
            "schema.name-mapping.default": json.dumps(name_mapping),
            **(extra_properties or {}),
        },
        "current-snapshot-id": snapshot_id,
        "refs": {"main": {"snapshot-id": snapshot_id, "type": "branch"}},
        "snapshots": [
            {
                "snapshot-id": snapshot_id,
                "sequence-number": sequence_number,
                "timestamp-ms": snapshot_id,
                "manifest-list": f"{location_uri}/metadata/{manifest_list_path.name}",
                "summary": {"operation": "append"},
                "schema-id": 0,
            }
        ],
        "snapshot-log": [{"snapshot-id": snapshot_id, "timestamp-ms": snapshot_id}],
        "metadata-log": [],
    }
    # V3 introduces row lineage. The spec carries both `last-row-id`
    # (a counter for the highest assigned row id) and `next-row-id`
    # (the next value to assign). Snowflake's V3 preview reader checks
    # for `last-row-id` and reports "incomplete state" if it's missing,
    # even on a fresh table where both are 0. Polaris (the reference
    # REST catalog) checks `next-row-id`. Emit both to satisfy the
    # strictest checker.
    if format_version_in_metadata >= 3:
        metadata["last-row-id"] = 0
        metadata["next-row-id"] = 0
        metadata["row-lineage"] = False
    metadata_json_path = meta_dir / "v1.metadata.json"
    metadata_json_path.write_text(json.dumps(metadata, indent=2))
    return metadata_json_path
