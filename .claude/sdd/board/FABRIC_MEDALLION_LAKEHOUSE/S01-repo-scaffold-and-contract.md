---
id: S01
feature: FABRIC_MEDALLION_LAKEHOUSE
title: "Scaffold the lab project and the canonical contract notebook"
status: ready
depends_on: []
agent: spark-engineer
files: [pyproject.toml, .gitignore, .env.example, workspace/nb_lib_contract.Notebook/notebook-content.py, tests/conftest.py, tests/test_contract.py, .github/workflows/ci.yml]
updated: 2026-10-02
---

## Story 1: Scaffold the lab project and the canonical contract notebook (pyproject.toml, nb_lib_contract, tests/conftest.py, tests/test_contract.py, ci.yml)

**Description:** Creates the root Python project, the CI workflow, and `nb_lib_contract`: the single in-Fabric definition of sources, tables, primary keys and the canonical Silver schema (DESIGN Pattern 1, Decision 5). This is the foundation; no other story can land before this one merges, because every notebook `%run`s the contract and every test uses the shared `conftest.py`. A contract test pins the notebook to the generator's schema so the two can't drift.

**Actual Plan**

1. `pyproject.toml` (root, new; separate from `sources/pyproject.toml` so the two deployable units share no dependencies):
   - `requires-python = ">=3.11,<3.12"` to match Fabric Runtime 1.3.
   - Runtime deps: `pyspark==3.5.*`, `pyarrow>=15`, `fabric-cicd`, `azure-identity`, `azure-storage-file-datalake`, `pyodbc`, `requests`.
   - Dev group: `pytest>=8`, `ruff`.
   - `[tool.pytest.ini_options]`: `testpaths = ["tests"]`, `markers = ["e2e: needs a deployed Fabric workspace"]`, `addopts = "-m 'not e2e'"`.
2. `.gitignore` (root, new): `.venv/`, `.env`, `__pycache__/`, `.pytest_cache/`, `sources/data/`, `spark-warehouse/`, `metastore_db/`.
3. `.env.example` (root, new): `AZURE_TENANT_ID=`, `AZURE_CLIENT_ID=`, `AZURE_CLIENT_SECRET=`, `FABRIC_WORKSPACE_ID=`, `FABRIC_ENV=DEV`.
4. `workspace/nb_lib_contract.Notebook/notebook-content.py` + `.platform` (new, Fabric Git notebook format with `# Fabric notebook source` / `# CELL ********************` markers):
   - Constants verbatim from DESIGN Pattern 1: `SOURCES`, `REFERENCE_SOURCE = "parquet"`, `DB_SOURCES`, `FILE_SOURCES = ["csv", "parquet", "json"]`, `DIMENSIONS`, `FACTS`, `TABLES`, `PRIMARY_KEYS`, `MEASURES`, `DERIVED_FROM_FATO001`, `FACT_FOREIGN_KEYS`.
   - `CANONICAL_SCHEMAS` for all 12 tables:
     - Facts mirror `sources/src/generator/facts.py:21-40`, with every float64 measure as `DecimalType(18,2)` and every column non-nullable.
     - Dimensions mirror `sources/src/generator/dims.py:11-23` and `sources/src/generator/timedim.py:13-21`. `dim_tempo.Data` is `DateType`; int32 columns are `IntegerType`; `dim_organizacional.Cod_Pai` is nullable.
   - PKs/FKs/measures/derived keys mirror `sources/src/generator/schema.py:21-30` (`PRIMARY_KEYS`), `:33-39` (`FACT_FOREIGN_KEYS`), `:49-53` (`MEASURES`) and `:56-61` (`DERIVED_FROM_FATO001`).
   - Top level holds only imports and constants: no Spark session use, no `display()`.
5. `tests/conftest.py` (new; pytest fixture style matching `sources/tests/conftest.py:7-21`):
   - `spark` session fixture: `SparkSession.builder.master("local[2]")`, `spark.sql.session.timeZone=UTC`, `spark.sql.ansi.enabled=false`, `spark.sql.shuffle.partitions=2`.
   - `load_notebook(name) -> dict`: `runpy.run_path("workspace/<name>.Notebook/notebook-content.py")`.
   - `fixture_data` session fixture: inserts `sources/src` on `sys.path`, calls `generator.pipeline.generate(load_config(**{"period.end": "2013-03-31"}), data_dir=tmp)` (`sources/src/generator/config.py:16`, `pipeline.py:16`) for all 6 formats, and returns the tmp path.
6. `tests/test_contract.py` (new, pytest): for each of the 12 tables, compare `CANONICAL_SCHEMAS[t]` against `generator.schema.TABLE_SCHEMAS[t]` (`schema.py:14`):
   - same column names, same order;
   - float64 ↔ Decimal(18,2), string ↔ String, int32 ↔ Integer, date32 ↔ Date, timestamp ↔ Timestamp;
   - `PRIMARY_KEYS`, `MEASURES` and `DERIVED_FROM_FATO001` equal the generator's.
7. `.github/workflows/ci.yml` (new):
   - Trigger: `pull_request` + `push` on `main`.
   - `actions/setup-java@v4` (temurin 17), `astral-sh/setup-uv`.
   - `uv sync`, `uv run ruff check .`, `uv run pytest`, then `cd sources && uv sync && uv run pytest`.

**Architecture**

```text
sources/src/generator/{schema,facts,dims,timedim}.py ──(import)──┐
                                                                  ▼
workspace/nb_lib_contract.Notebook ──(runpy)──► tests/test_contract.py  [assert equal]
        │
        └──(%run in Fabric)──► every later notebook (S02, S04, S05, S06)
ci.yml: ruff + pytest (lab) + pytest (sources)
```

**Acceptance Criteria**

- [ ] `uv sync && uv run pytest` passes locally and in `ci.yml` on a PR
- [ ] `tests/test_contract.py` fails if any canonical column, type, PK or measure differs from `sources/src/generator/schema.py` (verified by temporarily renaming one column)
- [ ] `nb_lib_contract` contains no Spark actions, `display()` or `spark.` references at top level (grep check)
- [ ] `CANONICAL_SCHEMAS` covers exactly the 12 tables in `generator.schema.LOAD_ORDER` (`schema.py:17`)
- [ ] All measures are `DecimalType(18,2)` and all codes are `StringType` in `CANONICAL_SCHEMAS`
- [ ] Root `pyproject.toml` doesn't depend on the `sources` package; `sources/` runs from its own project, with zero changes to `sources/pyproject.toml`
- [ ] `ci.yml` also runs the existing 16 `sources/tests` and they still pass
