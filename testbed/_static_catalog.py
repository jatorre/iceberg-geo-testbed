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
    AVRO_CODEC_KEY,
    DataFile,
    DataFileContent,
    FileFormat,
    ManifestEntry,
    ManifestEntryStatus,
    ManifestFile,
    ManifestListWriterV2,
    ManifestWriterV2,
    MANIFEST_LIST_FILE_SCHEMAS,
    UNASSIGNED_SEQ,
    construct_partition_summaries,
    write_manifest,
    write_manifest_list,
)
from pyiceberg.partitioning import PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.typedef import Record


# pyiceberg 0.11.1 has manifest schemas defined for V1/V2/V3 but the
# write_manifest() / write_manifest_list() entrypoints explicitly reject
# version=3. We unblock V3 writes by subclassing the V2 writers and just
# overriding the version property. The schemas keyed off `self.version`
# (V3 manifest entry schemas include the V3-only `first_row_id` field
# on data files; V3 manifest-list entries include `first_row_id` too).
class _ManifestWriterV3(ManifestWriterV2):
    def __init__(self, *args, schema_override_json: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        # Lets the caller swap in a custom schema JSON string for the
        # avro metadata. Needed for V3 GEOMETRY columns because
        # pyiceberg's PrimitiveType model has no GeometryType, so the
        # Python schema falls back to BinaryType — and that "binary"
        # type token then appears in the manifest avro metadata,
        # contradicting the table's claim that the column is geometry.
        # Snowflake reads this and rejects.
        self._schema_override_json = schema_override_json

    @property
    def version(self) -> int:  # type: ignore[override]
        return 3

    @property
    def _meta(self) -> dict[str, str]:
        m = {**super()._meta, "format-version": "3"}
        if self._schema_override_json is not None:
            m["schema"] = self._schema_override_json
        # `iceberg.schema` carries the manifest-entry record schema in
        # Iceberg JSON format. Snowflake's V3 writer emits this; we
        # mirror it for compatibility. The schema comes from pyiceberg's
        # MANIFEST_ENTRY_SCHEMAS_STRUCT keyed by version.
        from pyiceberg.manifest import MANIFEST_ENTRY_SCHEMAS_STRUCT
        try:
            m["iceberg.schema"] = MANIFEST_ENTRY_SCHEMAS_STRUCT[3].model_dump_json()
        except Exception:
            pass
        return m

    def new_writer(self):
        # pyiceberg's default `new_writer()` uses DEFAULT_READ_VERSION (=2)
        # for the *record* schema, even when the file schema is V3. That
        # silently drops V3-only fields like data_file.first_row_id
        # because the Python object's V3 positions aren't read. We
        # override to use our V3 version for both schemas so V3 fields
        # actually make it into the avro bytes.
        from pyiceberg.avro.file import AvroOutputFile
        from pyiceberg.manifest import ManifestEntry
        return AvroOutputFile[ManifestEntry](
            output_file=self._output_file,
            file_schema=self._with_partition(self.version),
            record_schema=self._with_partition(self.version),
            schema_name="manifest_entry",
            metadata=self._meta,
        )

    def to_manifest_file(self) -> ManifestFile:
        # Same as the parent but bind the ManifestFile to V3 so the
        # V3-only `first_row_id` slot exists on the Python object.
        # The caller is responsible for setting that slot afterwards
        # (manifest._data[15] = first_row_id_for_this_manifest).
        self.closed = True
        min_seq = self._min_sequence_number or UNASSIGNED_SEQ
        return ManifestFile.from_args(
            _table_format_version=3,
            manifest_path=self._output_file.location,
            manifest_length=len(self._writer.output_file),
            partition_spec_id=self._spec.spec_id,
            content=self.content(),
            sequence_number=UNASSIGNED_SEQ,
            min_sequence_number=min_seq,
            added_snapshot_id=self._snapshot_id,
            added_files_count=self._added_files,
            existing_files_count=self._existing_files,
            deleted_files_count=self._deleted_files,
            added_rows_count=self._added_rows,
            existing_rows_count=self._existing_rows,
            deleted_rows_count=self._deleted_rows,
            partitions=construct_partition_summaries(self._spec, self._schema, self._partitions),
            key_metadata=None,
            first_row_id=0,
        )


class _ManifestListWriterV3(ManifestListWriterV2):
    def __init__(self, output_file, snapshot_id, parent_snapshot_id, sequence_number, compression):
        # Call ManifestListWriter.__init__ directly so we control the
        # meta keys without going through the V2-hardcoded path.
        from pyiceberg.manifest import ManifestListWriter
        ManifestListWriter.__init__(
            self,
            format_version=3,
            output_file=output_file,
            meta={
                "snapshot-id": str(snapshot_id),
                "parent-snapshot-id": str(parent_snapshot_id) if parent_snapshot_id is not None else "null",
                "sequence-number": str(sequence_number),
                "format-version": "3",
                AVRO_CODEC_KEY: compression,
            },
        )
        self._commit_snapshot_id = snapshot_id
        self._sequence_number = sequence_number

    def __enter__(self):
        # Same fix as on the manifest entry writer — V3 record_schema, not
        # DEFAULT_READ_VERSION (which is 2). Otherwise the V3-only
        # first_row_id field on each ManifestFile entry is silently
        # dropped during avro serialization.
        from pyiceberg.avro.file import AvroOutputFile
        self._writer = AvroOutputFile[ManifestFile](
            output_file=self._output_file,
            record_schema=MANIFEST_LIST_FILE_SCHEMAS[self._format_version],
            file_schema=MANIFEST_LIST_FILE_SCHEMAS[self._format_version],
            schema_name="manifest_file",
            metadata=self._meta,
        )
        self._writer.__enter__()
        return self


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
    row_lineage: bool = False,
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

    # pyiceberg has manifest avro schemas defined for V1/V2/V3 but its
    # writer historically defaulted to V2. We pass V3 explicitly when
    # the table metadata claims V3, so the manifest avro is also V3-
    # spec-compliant (Snowflake's V3 reader requires this consistency;
    # V2 readers are more permissive and accept the V2 fixture path).
    manifest_format = 3 if format_version_in_metadata >= 3 else 2

    manifest_path = meta_dir / f"snap-{snapshot_id}-manifest.avro"
    if manifest_format >= 3:
        # Build the schema-override JSON string from schema_json_fields
        # — that's the caller-supplied fields list which already has
        # the correct V3 type tokens (e.g. "geometry") that pyiceberg's
        # PrimitiveType can't represent.
        schema_override = json.dumps({
            "type": "struct",
            "schema-id": 0,
            "fields": schema_json_fields,
        })
        mw_ctx = _ManifestWriterV3(
            spec=PartitionSpec(),
            schema=iceberg_schema,
            output_file=io.new_output(str(manifest_path)),
            snapshot_id=snapshot_id,
            avro_compression="null",
            schema_override_json=schema_override,
        )
    else:
        mw_ctx = write_manifest(
            format_version=manifest_format,
            spec=PartitionSpec(),
            schema=iceberg_schema,
            output_file=io.new_output(str(manifest_path)),
            snapshot_id=snapshot_id,
            avro_compression="null",
        )
    with mw_ctx as mw:
        for i, df in enumerate(data_files):
            data_file_args = dict(
                content=DataFileContent.DATA,
                file_path=f"{location_uri}/{df['path']}",
                file_format=FileFormat.PARQUET,
                partition=Record(),
                record_count=df["rows"],
                file_size_in_bytes=df["size"],
                lower_bounds=df["lower"],
                upper_bounds=df["upper"],
            )
            # Optional Iceberg metrics (column_sizes / value_counts /
            # null_value_counts). Spec-optional, but some readers — notably
            # Oracle ADB's Iceberg parser — treat them as required to
            # enumerate the column list. Populate when the fixture supplies
            # them.
            for _k in ("column_sizes", "value_counts", "null_value_counts"):
                if df.get(_k) is not None:
                    data_file_args[_k] = df[_k]
            if manifest_format >= 3:
                # V3 adds `first_row_id` to each data file — the row_id of
                # the first row in this file. For a fresh table where no
                # rows have been assigned ids, we use a monotonic counter
                # offset by row counts of preceding files. Required for
                # spec-compliant V3 manifests.
                data_file_args["first_row_id"] = sum(d["rows"] for d in data_files[:i])
                df_obj = DataFile.from_args(_table_format_version=3, **data_file_args)
                me_obj = ManifestEntry.from_args(
                    _table_format_version=3,
                    status=ManifestEntryStatus.ADDED,
                    snapshot_id=snapshot_id,
                    sequence_number=sequence_number,
                    file_sequence_number=sequence_number,
                    data_file=df_obj,
                )
            else:
                df_obj = DataFile.from_args(**data_file_args)
                me_obj = ManifestEntry.from_args(
                    status=ManifestEntryStatus.ADDED,
                    snapshot_id=snapshot_id,
                    sequence_number=sequence_number,
                    file_sequence_number=sequence_number,
                    data_file=df_obj,
                )
            mw.add_entry(me_obj)
    manifest = mw.to_manifest_file()
    # The manifest avro is written to the local meta_dir; in the manifest-list
    # we record it under `location_uri/metadata/...` so an engine pointing at
    # the remote copy of the catalog can resolve it. ManifestFile.manifest_path
    # has no setter, but ManifestFile is a Record backed by a mutable list at
    # _data — index 0 is manifest_path.
    manifest._data[0] = f"{location_uri}/metadata/{manifest_path.name}"

    manifest_list_path = meta_dir / f"snap-{snapshot_id}-manifest-list.avro"
    if manifest_format >= 3:
        mlw_ctx = _ManifestListWriterV3(
            output_file=io.new_output(str(manifest_list_path)),
            snapshot_id=snapshot_id,
            parent_snapshot_id=None,
            sequence_number=sequence_number,
            compression="null",
        )
    else:
        mlw_ctx = write_manifest_list(
            format_version=manifest_format,
            output_file=io.new_output(str(manifest_list_path)),
            snapshot_id=snapshot_id,
            parent_snapshot_id=None,
            sequence_number=sequence_number,
            avro_compression="null",
        )
    with mlw_ctx as mlw:
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
    # V3 metadata shape, validated empirically against a Snowflake-
    # managed V3 GEOMETRY table:
    #   - `next-row-id` required
    #   - `statistics` / `partition-statistics` required as arrays
    #     (empty is fine)
    #   - `last-row-id` and `row-lineage` are NOT present in
    #     Snowflake's output, even though Polaris accepts them
    # Snowflake's V3 reader rejects our metadata with "incomplete
    # state" when these extras are present or expected fields missing.
    # Matching Snowflake's exact shape gets past the rejection.
    if format_version_in_metadata >= 3:
        metadata["next-row-id"] = sum(d["rows"] for d in data_files)
        metadata["statistics"] = []
        metadata["partition-statistics"] = []
        # `row-lineage` is an explicit V3 flag. False (default here)
        # means data files don't carry the `_row_id` /
        # `_last_updated_sequence_number` metadata columns. True means
        # they MUST be present in every data file. The caller is
        # responsible for ensuring the parquet writer matches.
        metadata["row-lineage"] = row_lineage
    metadata_json_path = meta_dir / "v1.metadata.json"
    metadata_json_path.write_text(json.dumps(metadata, indent=2))
    return metadata_json_path
