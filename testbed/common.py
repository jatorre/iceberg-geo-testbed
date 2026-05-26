"""Shared fixtures and helpers for the engine-agnostic Iceberg test runners.

Determinism note: rebuilds of the same fixture in different Python processes
must produce byte-identical parquet files. Python's built-in `hash()` is
seeded per-process, so we use `stable_seed(name)` (hashlib-based) to derive
the per-region RNG seed instead.

Each runner builds a tiny "static catalog" — `metadata.json` + manifest avro
files on disk — over 10 disjoint world regions × 1000 synthetic rows each.
A correct file-level pruner should narrow any single-region bbox query to one
file. We grep `EXPLAIN ANALYZE` output for "Total Files Read: N" to assert.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class Region:
    name: str
    xmin: float
    xmax: float
    ymin: float
    ymax: float


# Ten disjoint world regions. Each runner generates ~1000 points constrained to
# the region's bbox, so the per-file aggregate bounds equal the region bounds.
REGIONS: list[Region] = [
    Region("pacific_far_west", -180.0, -150.0, -10.0,  10.0),
    Region("hawaii",           -160.0, -154.0,  18.0,  22.0),
    Region("california",       -125.0, -115.0,  32.0,  42.0),  # contains "SF" probe
    Region("texas",            -107.0,  -93.0,  26.0,  36.0),
    Region("ny",                -75.0,  -73.0,  40.0,  41.0),
    Region("uk",                 -6.0,    2.0,  50.0,  56.0),
    Region("rome",                9.0,   13.0,  41.0,  43.0),
    Region("india",              68.0,   88.0,  10.0,  30.0),
    Region("japan",             130.0,  146.0,  31.0,  45.0),
    Region("sydney",            148.0,  152.0, -35.0, -33.0),
]


# Probe bbox: tight-ish California window. Only the "california" region should
# match. Used as the pruning assertion query.
PROBE_BBOX = (-125.0, 32.0, -115.0, 42.0)  # west, south, east, north


def wkb_point_le(x: float, y: float) -> bytes:
    """Little-endian WKB encoding of POINT(x y). 21 bytes."""
    return struct.pack("<BIdd", 1, 1, x, y)


def double_le(v: float) -> bytes:
    """8-byte little-endian IEEE 754 double — the Iceberg numeric bound format."""
    return struct.pack("<d", v)


def packed_xy_le(x: float, y: float) -> bytes:
    """16 bytes: x then y as little-endian doubles. The Iceberg V3 spec format
    for 2D geometry bounds per the manifest encoding."""
    return struct.pack("<dd", x, y)


def stable_seed(name: str) -> int:
    """32-bit deterministic seed for `random.Random()`, derived from a stable
    hash of `name`. Python's builtin `hash()` is per-process-seeded, so using
    it would make fixture builds non-reproducible across processes."""
    h = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(h[:4], "little")


def parquet_metrics(path, field_ids: list[int]) -> tuple[dict, dict, dict]:
    """Compute the optional Iceberg per-file metrics from a parquet file's
    metadata: (column_sizes, value_counts, null_value_counts), each keyed by
    Iceberg field id.

    `field_ids` lists the field ids in parquet column order (1:1 for our flat,
    all-primitive fixtures). These metrics are spec-optional, but some readers
    (Oracle ADB) treat them as required to enumerate columns.
    """
    import pyarrow.parquet as pq

    md = pq.read_metadata(str(path))
    column_sizes = {fid: 0 for fid in field_ids}
    null_value_counts = {fid: 0 for fid in field_ids}
    value_counts = {fid: md.num_rows for fid in field_ids}
    for rg in range(md.num_row_groups):
        row_group = md.row_group(rg)
        for col in range(row_group.num_columns):
            fid = field_ids[col]
            cc = row_group.column(col)
            column_sizes[fid] += cc.total_compressed_size
            stats = cc.statistics
            if stats is not None:
                nc = getattr(stats, "null_count", None)
                if nc is not None:
                    null_value_counts[fid] += nc
    return column_sizes, value_counts, null_value_counts
