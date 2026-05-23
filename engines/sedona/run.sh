#!/usr/bin/env bash
# Launch the Sedona-on-Spark fixture builder inside Docker.
#
# Spark + Sedona + Iceberg are pulled via --packages, so first run is slow
# (couple hundred MB of jars get downloaded into the container layer's Ivy
# cache; ~/.ivy2 on the host is bind-mounted so subsequent runs are fast).

set -euo pipefail

REPO="$(cd "$(dirname "$0")"/../.. && pwd)"

# apache/sedona:1.6.1 ships with Spark 3.4.1 / Scala 2.12 inside.
ICEBERG_PKG="org.apache.iceberg:iceberg-spark-runtime-3.4_2.12:1.7.1"
# Sedona Maven coords matching the bundled Sedona version (1.6.1, Spark 3.4).
SEDONA_PKG="org.apache.sedona:sedona-spark-3.4_2.12:1.6.1,org.datasyslab:geotools-wrapper:1.6.1-28.2"

mkdir -p "$REPO/engines/sedona/work" "$HOME/.ivy2"

# SCRIPT picks build_fixtures.py (default) or "probe" to run probe.py.
SCRIPT="${1:-build}"
case "$SCRIPT" in
  build) PYFILE="$REPO/engines/sedona/build_fixtures.py" ;;
  probe) PYFILE="$REPO/engines/sedona/probe.py" ;;
  *)     echo "usage: $0 [build|probe]"; exit 1 ;;
esac

# Bind-mount the repo at its host path inside the container too, so
# file:///Users/jatorre/... URIs in our hand-written metadata resolve.
exec docker run --rm \
  --entrypoint /opt/spark/bin/spark-submit \
  -e PYSPARK_DRIVER_PYTHON=python3 \
  -e PYSPARK_PYTHON=python3 \
  -v "$REPO":"$REPO" \
  -v "$HOME/.ivy2":/root/.ivy2 \
  -w "$REPO" \
  apache/sedona:1.6.1 \
    --packages "$ICEBERG_PKG,$SEDONA_PKG" \
    --conf spark.driver.memory=2g \
    "$PYFILE"
