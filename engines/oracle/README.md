# Oracle Autonomous Database engine runner

Status as of **2026-05-23.** Oracle **AI Database 26ai** Enterprise Edition
`23.26.2.2.0` on Oracle Autonomous Database serverless (Always Free tier
service `acmefreetier_high`), via `python-oracledb` thin-mode with the
wallet from the `carto-dev-database-credentials` gcloud secret.

## Headline finding

Oracle's Iceberg reader **rejects pyiceberg-emitted metadata** with the
generic error `ORA-20000: Iceberg parameter error / Failed to generate
column list`, despite the same files being readable by DuckDB, BigQuery,
and Sedona/Iceberg-Spark.

The rejection is consistent across:
- All three fixtures (v2_flat_columns, v2_bbox_struct, v3_geometry).
- URI variants (path to `metadata.json` vs path to the table root;
  `format-version=2` vs `=3`).
- Format-JSON variants (minimal `{"protocol_type":"iceberg"}`, with
  `protocol_config.iceberg_metadata_file_uri`, etc.).
- Schemes inside the metadata (`gs://...` vs
  `https://storage.googleapis.com/...`).

Public-bucket network access is verified working: `DBMS_CLOUD.LIST_OBJECTS`
returns the parquet file names correctly. So it's not a network or ACL
issue. We **did** need to grant outbound HTTPS to `storage.googleapis.com`
via `DBMS_NETWORK_ACL_ADMIN.APPEND_HOST_ACE`; that worked.

So the wall is at Oracle's **Iceberg manifest parser**, which apparently
expects something we don't emit. Oracle's docs say the supported
producers are Spark/Athena/Snowflake — and a quick comparison of our
manifest avro against Sedona/Iceberg-Spark's (we have that diff in
`engines/sedona/`) shows we omit `column_sizes`, `value_counts`,
`null_value_counts`, and string-column bounds. One of those may be the
field Oracle's parser treats as required even though the Iceberg spec
itself marks them optional.

## L0–L4 matrix row

| Fixture | Level |
|---|---|
| `v2_flat_columns` | **L0** — `ORA-20000: Iceberg parameter error` |
| `v2_bbox_struct`  | **L0** — same |
| `v3_geometry`     | **L0** — same |

## What an interop fix would look like

Either:
- Have our hand-written metadata pad the extra Spark/Iceberg fields
  (`column_sizes`, `value_counts`, `null_value_counts`, string-column
  bounds in `lower_bounds`/`upper_bounds`). Easy enough — `_static_catalog.py`
  could be extended.
- Or use Sedona/Iceberg-Spark to produce the fixture; upload that.
  That's the cleanest cross-engine canonical path.

Neither was worth pursuing in this session — Oracle's V3 geometry support
status is the more interesting question for the matrix and that's
unanswered until Oracle starts accepting V3 metadata at all.

## What works

- Network: outbound HTTPS allowlist via
  `DBMS_NETWORK_ACL_ADMIN.APPEND_HOST_ACE('storage.googleapis.com', ...)`
- Public-bucket read: `DBMS_CLOUD.LIST_OBJECTS` returns the data files.
- Iceberg path-based registration syntax (verified against the docs):

  ```sql
  BEGIN
    DBMS_CLOUD.CREATE_EXTERNAL_TABLE(
      table_name    => 'V2_FLAT_COLUMNS',
      file_uri_list => 'https://.../metadata/v1.metadata.json',
      format        => '{"access_protocol":{"protocol_type":"iceberg"}}'
    );
  END;
  ```

  Public bucket means `credential_name` is not needed. Network ACL is.

## Files

- `_creds.py` — loads user/password + wallet location from gcloud secret.
- (Wallet itself is extracted out-of-band into
  `~/.config/iceberg-geo-testbed/oracle-wallet/`; see one-time setup.)
- The wallet's TNS service name is `acmefreetier_high` (the secret's
  stale `cartoci_high` claim notwithstanding).

## One-time wallet extraction

```bash
mkdir -p ~/.config/iceberg-geo-testbed/oracle-wallet
gcloud secrets versions access latest --secret=carto-dev-database-credentials --project=cartobq \
  | python3 -c "import json,sys,base64; sys.stdout.buffer.write(base64.b64decode(json.load(sys.stdin)['databases']['oracle']['config']['credentials']['walletZip']))" \
  > ~/.config/iceberg-geo-testbed/oracle-wallet.zip
unzip ~/.config/iceberg-geo-testbed/oracle-wallet.zip -d ~/.config/iceberg-geo-testbed/oracle-wallet/
chmod 700 ~/.config/iceberg-geo-testbed/oracle-wallet
chmod 600 ~/.config/iceberg-geo-testbed/oracle-wallet/*
rm ~/.config/iceberg-geo-testbed/oracle-wallet.zip
```
