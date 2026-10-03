---
id: S04
feature: FABRIC_MEDALLION_LAKEHOUSE
title: "Ingest all six sources into Bronze with lineage and watermarks"
status: backlog
depends_on: [S02, S03]
agent: fabric-pipeline-developer
files: [scripts/upload_files.py, scripts/run_pipeline.py, workspace/nb_bronze_load.Notebook/notebook-content.py, workspace/pl_fruitjuice_e2e.DataPipeline/pipeline-content.json, workspace/parameter.yml, tests/e2e/conftest.py, tests/e2e/test_bronze_counts.py]
updated: 2026-10-02
---

## Story 4: Ingest all six sources into Bronze with lineage and watermarks (upload_files.py, run_pipeline.py, nb_bronze_load, pl_fruitjuice_e2e, e2e tests)

**Description:** First vertical slice that runs in Fabric. Files are uploaded to OneLake. The pipeline copies the three databases through the gateway into `Files/landing`, and `nb_bronze_load` writes the 73 `brz_<source>_<table>` Delta tables with lineage columns, watermarks and a load log (DESIGN Decisions 1 and 7, Pattern 4). It creates `pl_fruitjuice_e2e`, which S05 and S06 extend. Prerequisite for S05.

**Actual Plan**

1. `scripts/upload_files.py` (new):
   - CLI `--workspace-id`, `--formats csv,parquet,json`.
   - Uses `DataLakeServiceClient("https://onelake.dfs.fabric.microsoft.com", credential=DefaultAzureCredential())`, file system = workspace id.
   - Uploads `sources/data/<fmt>/fruit_juice/**` to `lh_bronze.Lakehouse/Files/upload/<fmt>/`, keeping relative paths (parquet `year=YYYY/` partitions intact). It deletes the target `<fmt>/` folder first (replace semantics).
   - Prints the file count and bytes per format.
   - Fails with exit 1 if `sources/data/<fmt>/fruit_juice` is missing, suggesting `uv run generator generate`.
2. `scripts/run_pipeline.py` (new):
   - `POST /v1/workspaces/{ws}/items/{pipeline_id}/jobs/instances?jobType=Pipeline` with `executionData.parameters`.
   - Polls the `Location` header every 30 s until `Completed` / `Failed` / `Cancelled`; exits 0 only on `Completed`.
   - Resolves the pipeline id by display name `pl_fruitjuice_e2e` via `GET /v1/workspaces/{ws}/items?type=DataPipeline`.
   - CLI flag `--param key=value` (repeatable).
3. `workspace/nb_bronze_load.Notebook/notebook-content.py` + `.platform` (new; default lakehouse `lh_bronze`):
   - Parameter cell: `batch_id`, `full_reload=False`, `sources="parquet,csv,json,sqlserver,postgresql,mongodb"`.
   - `%run nb_lib_contract` and `%run nb_lib_adapters`.
   - `READERS` and `source_path` as in DESIGN Pattern 4:
     - CSV with `header=True` and `inferSchema=False`;
     - JSON for `json` and `mongodb` landing;
     - Mongo also loads `sales` → `brz_mongodb_sales`.
   - Facts: filter `> watermark` for file sources (`DATA_FATO`; Mongo `sales` uses `data.$date`), then append. Dims: overwrite.
   - Creates `ctl_watermark(source STRING, table_name STRING, max_data_fato TIMESTAMP, updated_at TIMESTAMP)` and `ctl_load_log(batch_id, source, table_name, rows BIGINT, seconds DOUBLE, loaded_at)` if missing. This notebook never advances watermarks; that's S06.
   - `full_reload=True`: overwrite fact tables and ignore watermarks.
   - Ends with `notebookutils.notebook.exit(json.dumps({"rows": {...}}))`.
4. `workspace/pl_fruitjuice_e2e.DataPipeline/pipeline-content.json` + `.platform` (new). Parameters: `batch_id` (default `@pipeline().RunId`), `full_reload` (bool, false). Activities:
   - `Lookup_Watermarks`: query `ctl_watermark` through the `lh_bronze` SQL endpoint.
   - 3 `ForEach` activities in parallel (`sqlserver`, `postgresql`, `mongodb`), each over the 12 tables (Mongo: 12 + `sales`). Each runs a `Copy` from schema `fruit_juice` (created by `sources/src/generator/writers/sqlscripts.py:136`) in database `labsources`:
     - **Source query:** `SELECT * FROM fruit_juice.<t> WHERE DATA_FATO > '<wm>'` for facts, `SELECT *` for dims. Postgres uses lower-case `data_fato`; Mongo uses a `filter` on `DATA_FATO` (`data` for `sales`).
     - **Sink:** `lh_bronze` Files at `landing/<src>/<t>/@{pipeline().parameters.batch_id}/` — Parquet for the SQL sources, JSON for Mongo.
     - **Retry:** 2 × 60 s.
   - `nb_bronze_load` notebook activity, `On success` of all three ForEach activities.
   - Connection ids are placeholders `00000000-0000-0000-0000-00000000c001/2/3`.
5. `workspace/parameter.yml`: add the 3 connection `find_replace` entries plus `lh_bronze` id (`$items.Lakehouse.lh_bronze.$id`) for `item_type: DataPipeline` and `Notebook` (DESIGN Pattern 7).
6. `tests/e2e/conftest.py` (new): `pyodbc` connection to the `lh_bronze` SQL endpoint, using an SPN access token via `azure-identity` (`SQL_COPT_SS_ACCESS_TOKEN = 1256`). Env `FABRIC_BRONZE_SQL_ENDPOINT`. Every test carries `pytest.mark.e2e`.
7. `tests/e2e/test_bronze_counts.py` (new):
   - Exactly 73 tables matching `brz_%`.
   - For each source × table, the row count equals the local count from `sources/data/parquet/fruit_juice` (pyarrow). Mongo `sales` equals the `tab_fato001` count.
   - Every row has non-null `_source`, `_ingested_at`, `_batch_id`.

**Architecture**

```text
local: upload_files.py ──► lh_bronze/Files/upload/{csv,parquet,json}/
pl_fruitjuice_e2e:
  Lookup_Watermarks ─► ForEach{sqlserver,postgresql,mongodb} Copy(gateway) ─► Files/landing/<src>/<t>/<batch>/
                    └──────────────────────────────┬──────────────────────────────┘
                                                   ▼
                     nb_bronze_load ─► brz_<src>_<t> (73)  [+_source,_ingested_at,_batch_id]
                                     └► ctl_load_log        [watermark read only]
```

**Acceptance Criteria**

- [ ] After `upload_files.py` and one pipeline run on empty dev, exactly 73 `brz_*` tables exist; `tests/e2e/test_bronze_counts.py` passes
- [ ] Every `brz_*` row has non-null `_source`, `_ingested_at`, `_batch_id`, and `_batch_id` equals the pipeline RunId
- [ ] CSV Bronze columns are all `string` (no type inference); Mongo `sales` keeps nested `cliente`/`produto`/`fabrica`/`organizacional` structs
- [ ] With the Postgres container stopped, the run fails at the Postgres Copy, `nb_bronze_load` does not run, and no `brz_postgresql_*` table changes (AT-008)
- [ ] `ctl_watermark` is created but not advanced by this story's notebook
- [ ] No real connection GUIDs or credentials in `pipeline-content.json` (placeholders only; values come from `parameter.yml`)
- [ ] `run_pipeline.py` exits non-zero when the run fails
