# Apache Sedona engine runner — TODO

Sedona is the closest thing to a ground-truth V3 geo implementation today —
their Havasu predecessor became upstream Iceberg V3. A Sedona-on-Docker runner
serves two purposes:

1. **Reference implementation**: build the same fixture tables via Sedona's
   writer (which natively understands `GeometryType`), and diff the resulting
   manifest avro against ours. Differences will reveal the canonical bound
   encoding DuckDB / others will eventually adopt.
2. **Pruning oracle**: run the same probe query and confirm that file-level
   pruning narrows to 1 file. Establishes the "ground truth" the other engine
   rows should converge on.

Plan:
- `docker-compose.yml` with Apache Sedona's published image
- Spark job that reads our parquet files and writes a V3 Iceberg table
- Query script that emits per-file scan stats
