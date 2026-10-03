---
id: S05
feature: FABRIC_MEDALLION_LAKEHOUSE
title: "Build Silver staging, data-quality gates, and the convergence publish step"
status: backlog
depends_on: [S04]
agent: spark-engineer
files: [workspace/nb_silver_adapt.Notebook/notebook-content.py, workspace/nb_silver_converge.Notebook/notebook-content.py, workspace/pl_fruitjuice_e2e.DataPipeline/pipeline-content.json, workspace/parameter.yml]
updated: 2026-10-02
---

## Story 5: Build Silver staging, data-quality gates, and the convergence publish step (nb_silver_adapt, nb_silver_converge, pipeline)

**Description:** Runs the S02 library in Fabric. Each source's Bronze tables become canonical `stg_<source>_<table>` tables, the DQ gates block bad data, and the convergence step either fails the pipeline with a logged diff or MERGEs the reference source into the published `lh_silver.<table>` tables (DESIGN Decision 2, Data Quality Gates). This delivers G2/AT-002 in Fabric. Prerequisite for S06, since Gold reads only published Silver.

**Actual Plan**

1. `workspace/nb_silver_adapt.Notebook/notebook-content.py` + `.platform` (new; default lakehouse `lh_silver`, reads `lh_bronze` via the `lh_bronze.brz_*` qualified name):
   - Parameter cell: `batch_id`, `sources`. `%run nb_lib_contract` and `%run nb_lib_adapters`.
   - For each source × table: read `lh_bronze.brz_<src>_<t>`, apply `dedup_latest(df, PRIMARY_KEYS[t])` (from S02), apply `ADAPTERS[src]`, and overwrite `stg_<src>_<t>` (`overwriteSchema=true`).
   - Mongo: `tab_fato001`/`tab_fato002` come from `unnest_mongo_sales(lh_bronze.brz_mongodb_sales)`, not from the flat collections.
   - DQ gates on every `stg_*`:
     - PK not null and unique;
     - FK left-anti join count = 0, for each `FACT_FOREIGN_KEYS` column against the same source's `stg_<src>_<dim>`;
     - measures ≥ 0 and not null;
     - on the reference source only, `DERIVED_FROM_FATO001` key-set equality (logic as in `sources/src/generator/validate.py:97-101`).
   - Collect all violations, then `raise RuntimeError` listing `source.table: rule = count`. Staging tables stay in place for inspection.
   - Exit value: JSON of staged row counts.
2. `workspace/nb_silver_converge.Notebook/notebook-content.py` + `.platform` (new):
   - Parameter cell: `batch_id`.
   - Creates `ctl_convergence_log(batch_id, table_name, source, missing BIGINT, extra BIGINT, sample_keys STRING, checked_at)`.
   - Loop exactly as DESIGN Pattern 3, with `REFERENCE_SOURCE = "parquet"`. `sample_keys` holds the JSON of up to 5 PK tuples.
   - On any failure, raise `RuntimeError("Convergence failed - Gold not published: ...")`.
   - On pass:
     - for each table, `DeltaTable.forName(spark, t).merge(stg_parquet_t, pk_condition).whenMatchedUpdateAll().whenNotMatchedInsertAll()`, creating the table from staging on the first run;
     - then `OPTIMIZE <t> VORDER`.
   - Computes `from_date` = min `DATA_FATO` across the Bronze fact rows of `batch_id` (from `lh_bronze.brz_parquet_*` where `_batch_id = batch_id`), or `'9999-12-31'` when there are none, or `'1900-01-01'` when `full_reload`.
   - `notebookutils.notebook.exit(json.dumps({"from_date": ..., "tables": 12}))`.
3. `workspace/pl_fruitjuice_e2e.DataPipeline/pipeline-content.json`: append two activities after `nb_bronze_load`, each `On success` of the previous, with retry 1 × 300 s (capacity throttling):
   - `nb_silver_adapt`;
   - `nb_silver_converge` (pass `batch_id`, `full_reload`).
4. `workspace/parameter.yml`: add `lh_silver` id (`$items.Lakehouse.lh_silver.$id`) for the two notebooks.

**Architecture**

```text
lh_bronze.brz_<src>_<t> ─► dedup_latest ─► ADAPTERS[src] ─► lh_silver.stg_<src>_<t>
                                                  │ DQ gates: PK / FK / ≥0 / derived keys  [raise → stop]
                                                  ▼
nb_silver_converge: diff_counts(stg_parquet_t, stg_<src>_t) ×5 sources ×12 tables
     ├─ any ≠ (0,0) ─► ctl_convergence_log + RuntimeError ─► pipeline stops (no Gold)
     └─ all (0,0)   ─► MERGE stg_parquet_t ─► lh_silver.<t>  [OPTIMIZE VORDER] ─► exit {from_date}
```

**Acceptance Criteria**

- [ ] A clean run produces 72 `stg_*` tables (6 × 12) and 12 published canonical tables in `lh_silver`, all matching `CANONICAL_SCHEMAS`
- [ ] `ctl_convergence_log` has exactly 60 rows per batch (5 non-reference sources × 12 tables), all with `missing = 0` and `extra = 0` on a clean run
- [ ] After `UPDATE fruit_juice.tab_fato001 SET FATURAMENTO = FATURAMENTO + 0.01` on one Postgres row and a rerun, the pipeline fails at `nb_silver_converge`, the log names `postgresql`/`tab_fato001` with the row's key in `sample_keys`, and published `lh_silver.tab_fato001` is unchanged: same Delta version (AT-002)
- [ ] A DQ violation (e.g. a null PK injected into a Bronze table) fails `nb_silver_adapt` with a message naming source, table and rule, and `nb_silver_converge` does not run
- [ ] Rerunning with no new data leaves each published table's row count unchanged, and MERGE adds 0 rows
- [ ] Neither notebook redefines adapter logic; both only call `nb_lib_adapters` functions (grep: no `def adapt_` outside the library)
