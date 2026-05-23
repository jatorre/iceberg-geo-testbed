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
) -> Path:
    """Write metadata.json + manifest + manifest-list for a table.

    Args:
      table_root: filesystem directory containing `data/` (parquets) and where
        `metadata/` will be created.
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

    Returns:
      Path to the metadata.json file.
    """
    meta_dir = table_root / "metadata"
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
                        file_path=f"file://{(table_root / df['path']).resolve()}",
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
        "location": f"file://{table_root.resolve()}",
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
        },
        "current-snapshot-id": snapshot_id,
        "refs": {"main": {"snapshot-id": snapshot_id, "type": "branch"}},
        "snapshots": [
            {
                "snapshot-id": snapshot_id,
                "sequence-number": sequence_number,
                "timestamp-ms": snapshot_id,
                "manifest-list": f"file://{manifest_list_path.resolve()}",
                "summary": {"operation": "append"},
                "schema-id": 0,
            }
        ],
        "snapshot-log": [{"snapshot-id": snapshot_id, "timestamp-ms": snapshot_id}],
        "metadata-log": [],
    }
    metadata_json_path = meta_dir / "v1.metadata.json"
    metadata_json_path.write_text(json.dumps(metadata, indent=2))
    return metadata_json_path
