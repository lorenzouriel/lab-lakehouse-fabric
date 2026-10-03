---
id: S02
feature: FABRIC_MEDALLION_LAKEHOUSE
title: "Implement the six source adapters and the convergence check"
status: backlog
depends_on: [S01]
agent: spark-engineer
files: [workspace/nb_lib_adapters.Notebook/notebook-content.py, tests/test_adapters.py, tests/test_convergence.py]
updated: 2026-10-02
---

## Story 2: Implement the six source adapters and the convergence check (nb_lib_adapters, tests/test_adapters.py, tests/test_convergence.py)

**Description:** Delivers the core logic of the project as pure PySpark functions: one adapter per source that maps the native shape onto `CANONICAL_SCHEMAS`, the Mongo `sales` unnest, lineage columns, and the exact `exceptAll` convergence check (DESIGN Patterns 2–3, Decisions 2 and 5). Everything is tested locally against the 3-month fixture in all six formats. This story is a prerequisite for S04 (`with_lineage`) and S05 (adapters + `diff_counts`).

**Actual Plan**

1. `workspace/nb_lib_adapters.Notebook/notebook-content.py` + `.platform` (new). The first cell is `# MAGIC %run nb_lib_contract`. Local tests inject the contract namespace with `runpy.run_path(..., init_globals=contract)`.
   - `to_canonical(df, table) -> DataFrame`: case-insensitive name match; cast to the `CANONICAL_SCHEMAS` field type; a missing column raises `KeyError(f"{table}.{column}")`.
   - `adapt_csv`: `to_canonical` only. Bronze CSV is read with `inferSchema=false`, so codes are strings.
   - `adapt_parquet` / `adapt_sqlserver`: `to_canonical`, dropping a `year` partition column if present (`sources/src/generator/validate.py:34` shows the generator does the same).
   - `adapt_postgresql`: `to_canonical`. Identifiers arrive lower-cased (`sources/src/generator/writers/sqlscripts.py:56`).
   - `adapt_json`: `F.to_timestamp("DATA_FATO")` on the `YYYY-MM-DDTHH:MM:SS.000` text written by `writers/jsonl.py:56`.
   - `adapt_mongodb`: unwrap `{"$date": ...}` (`writers/jsonl.py:62`) with `F.to_timestamp(F.col("`DATA_FATO`.`$date`"))`, drop `_id`, then `to_canonical`.
   - `unnest_mongo_sales(sales) -> {"tab_fato001": df, "tab_fato002": df}`, using the field map from `writers/mongodb.py:102-142`:
     - `data.$date` → DATA_FATO, `cod_dia` → COD_DIA;
     - `cliente.cod`, `produto.cod`, `fabrica.cod`, `organizacional.cod` → the four codes;
     - `faturamento`, `imposto`, `custo_variavel`, `unidades` → UNIDADE_VENDIDA, `quantidade` → QUANTIDADE_VENDIDA, `frete` → FRETE.
   - `ADAPTERS = {source: fn}` for all 6 sources.
   - `with_lineage(df, source, batch_id)`: adds `_source`, `_ingested_at` (`current_timestamp()`) and `_batch_id`.
   - `dedup_latest(df, pk)`: `row_number()` over the PK ordered by `_ingested_at desc`, keep 1. Used by S05.
   - `diff_counts(reference, candidate) -> (missing:int, extra:int, sample:DataFrame)`, exactly as DESIGN Pattern 3.
   - No Spark actions at top level.
2. `tests/test_adapters.py` (new, pytest; parametrized style like `sources/tests/test_generator.py:36-63`). It reads `fixture_data` with the same readers S04 will use: CSV header/no-infer, parquet, json, and Mongo flat/`sales` JSONL via `spark.read.json`. SQL Server/Postgres are simulated by Parquet with lower-cased columns for Postgres and `DOUBLE` measures.
   - `test_every_adapter_matches_canonical_schema[source-table]`: 6 × 12 cases, `df.schema == CANONICAL_SCHEMAS[t]` (AT-005 included for Postgres).
   - `test_codes_keep_leading_zeros[source]`: `COD_FABRICA` contains `"002"` and never `"2"` (AT-003).
   - `test_mongo_sales_unnests_to_parquet_001_002`: `diff_counts` against the parquet adapter output == (0, 0) for both tables (AT-004).
   - `test_decimal_cast_identical_across_sources`: the FATURAMENTO sum and the per-row `exceptAll` agree for csv vs parquet vs json vs mongo (A-004).
   - `test_missing_column_raises_keyerror`.
3. `tests/test_convergence.py` (new, pytest):
   - `test_clean_sources_converge`: all 6 × 12 return (0, 0).
   - `test_mutated_value_detected`: +0.01 on one `FATURAMENTO` row gives missing=1, extra=1, and the sample contains that row's PK.
   - `test_deleted_row_detected`: missing=1, extra=0.
   - `test_duplicated_row_detected`: extra=1. This is why `exceptAll` is used rather than `except`.

**Architecture**

```text
brz-shaped DataFrame ─► ADAPTERS[source](df, table) ─► to_canonical ─► canonical DF
mongo sales ─► unnest_mongo_sales ─► {tab_fato001, tab_fato002} ─► to_canonical
canonical(parquet) ─┐
canonical(other) ───┴─► diff_counts ─► (missing, extra, sample)   [(0,0) == converged]
```

**Acceptance Criteria**

- [ ] All 6 sources × 12 tables produce exactly `CANONICAL_SCHEMAS[table]`; covered by `tests/test_adapters.py`
- [ ] Mongo `sales` unnest equals the parquet `tab_fato001` and `tab_fato002` with `diff_counts == (0, 0)` on the fixture
- [ ] No canonical code column ever loses a leading zero (test asserts `"002"` present and `"2"` absent)
- [ ] `diff_counts` detects mutated, deleted and duplicated rows (3 tests in `tests/test_convergence.py`), and the sample shows the offending key
- [ ] `to_canonical` raises `KeyError` naming table and column when a column is missing; it never fills nulls silently
- [ ] `nb_lib_adapters` has no top-level Spark actions; the only magic line is `# MAGIC %run nb_lib_contract`
- [ ] Zero new dependencies beyond S01's `pyproject.toml`
