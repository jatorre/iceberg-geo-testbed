# Sedona / Iceberg-Spark engine runner

Status as of **2026-05-23.** Sedona 1.6.1 + iceberg-spark-runtime 1.7.1 on
Spark 3.4.1 (Scala 2.12), inside `apache/sedona:1.6.1` Docker image.

## Results

Probing our hand-written fixtures (`engines/sedona/probe.py`):

| Fixture | Level | Detail |
|---|---|---|
| `v2_flat_columns` | **L3** | distinct input files contributing to result: 1 of 10 — manifest-level pruning works |
| `v2_bbox_struct`  | **L3** | 1 of 10 — prunes through struct fields too (matches BigQuery; better than DuckDB) |
| `v3_geometry`     | **L0** | `IllegalArgumentException: Cannot parse type string to primitive: geometry(OGC:CRS84)` — Iceberg-Spark 1.7.1 doesn't recognize the V3 geometry type token |

## Reproducing

You need Docker (OrbStack / Docker Desktop). The repo path needs to be
identical inside the container so the local `file://` URIs in our
hand-written metadata resolve — `run.sh` bind-mounts at `$REPO:$REPO`.

```bash
# Build local fixtures first (any DuckDB run does this)
python -m testbed.v2_flat_columns
python -m testbed.v2_bbox_struct
python -m testbed.v3_geometry

# Probe — assess L0-L4 by reading our metadata
engines/sedona/run.sh probe

# Build Sedona-native fixtures (for the ground-truth diff)
engines/sedona/run.sh build

# Diff our manifest avro vs Sedona's
python engines/sedona/diff_manifests.py
```

First Spark startup takes ~30s of jar resolution (Iceberg + Sedona +
geotools-wrapper); subsequent runs reuse the `~/.ivy2` cache so they're
~10s.

## Ground-truth bound-encoding diff (the original purpose)

The big motivation for Sedona was: produce the same fixture via the
"official" V3 writer and diff its manifest avro against ours.

What we got:

### V2 numeric bounds — bit-identical

Sedona-built `v2_flat_columns` manifest:
- `lower_bounds[xmin] = hex 49677251e8015140 = 68.0298...` (per-region min)
- `upper_bounds[xmin] = hex 08b26529baff5540 = 87.9957...` (per-region max)

Our hand-written manifest:
- `lower_bounds[xmin] = hex 00000000008066c0 = -180.0` (declared region bbox)
- `upper_bounds[xmin] = hex 0000000000c062c0 = -150.0`

The **byte encoding is identical** — little-endian IEEE 754 doubles. The
*values* differ because Sedona uses the actual per-file min/max while we
record the declared region bbox (which is conservative — wider but valid).

### Minor deltas in what Sedona populates that we skip

| Field | Ours | Sedona |
|---|---|---|
| `lower_bounds[id]` (string) | skipped | `b'india-0'` (UTF-8 of min string) |
| `upper_bounds[id]` (string) | skipped | `b'india-999'` |
| `column_sizes` | omitted | populated per column |
| `value_counts` | omitted | populated |
| `null_value_counts` | omitted | populated |
| `file_path` URI scheme | `file://` | bare path |

None of these matter for file-level *spatial* pruning. They're useful for
non-spatial query planning (string predicates, column-projection cost
estimation) which the testbed isn't measuring.

### V3 geometry — Sedona can't write it either

`CREATE TABLE ... geom GEOMETRY USING iceberg` (SQL form) fails with
`[UNSUPPORTED_DATATYPE]` — Spark 3.4's SQL parser doesn't know `GEOMETRY`.

DataFrame API (`df.writeTo(table).using('iceberg').create()` with a
Sedona-Geometry-typed column) gets further but ultimately fails with:

```
java.lang.UnsupportedOperationException: User-defined types are not supported
    at org.apache.iceberg.spark.SparkTypeVisitor.visit(SparkTypeVisitor.java:52)
```

So **iceberg-spark-runtime 1.7.1 does not have a UDT → IcebergGeometryType
mapper** — even though Sedona is the reference implementation team for the
V3 geometry spec. This is a real gap in the official toolchain as of
2026-05.

The `build_fixtures.py` script falls back to a `BINARY` column (WKB blobs)
in that case, which produces a V3-claimed-but-V2-shaped table — not the
ground truth we wanted for V3 geometry bounds.

## Files

- `build_fixtures.py` — PySpark script: builds the V2 fixture via Sedona's
  writer, attempts V3 geometry write (currently falls back to BINARY).
- `probe.py` — PySpark script: reads our hand-written fixtures and reports
  L0-L4 per case via Spark + Sedona.
- `diff_manifests.py` — host-side: reads Sedona's and our manifest avros
  and prints a side-by-side diff of the per-data-file rows.
- `run.sh` — Docker launcher. `run.sh build` runs `build_fixtures.py`;
  `run.sh probe` runs `probe.py`.

## Open follow-ups

- File an upstream Iceberg-Spark issue (or PR) for the UDT → GeometryType
  mapper. Without it, no Spark-based engine can produce V3 geometry Iceberg
  tables today.
- Sedona ships its own "spark-iceberg" extension (Wherobots-flavored)
  separately from the apache/sedona Docker image. Worth trying that build
  to see if they've already wired the UDT mapper there.
- Row-group pruning (L4) — not currently measured. Would need scan-level
  metrics from `SparkListenerExecutorMetricsUpdate`.
