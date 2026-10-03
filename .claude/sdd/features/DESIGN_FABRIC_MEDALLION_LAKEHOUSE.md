# DESIGN: Fabric Medallion Lakehouse (multi-source convergence)

> Technical design for ingesting the Fruit Juice dataset from six source shapes into Fabric, converging them in Silver, and serving a reconciled Gold star schema through Direct Lake

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | FABRIC_MEDALLION_LAKEHOUSE |
| **Date** | 2026-10-02 |
| **Author** | design-agent |
| **DEFINE** | [DEFINE_FABRIC_MEDALLION_LAKEHOUSE.md](./DEFINE_FABRIC_MEDALLION_LAKEHOUSE.md) |
| **Status** | Ready for Build |
| **Design Confidence** | 0.90. KB patterns (`microsoft-fabric`, `medallion`) + agent matches; three novel points validated against Microsoft Learn / fabric-cicd docs (see Assumption Resolution) |

---

## Architecture Overview

```text
┌─────────────────────────────── LOCAL MACHINE (Windows) ───────────────────────────────┐
│  sources/  uv run generator generate (seed 42)  ──►  sources/data/{csv,parquet,json,  │
│                                                         mongodb,postgresql,sqlserver}  │
│            uv run generator load  ──►  docker: SQL Server :1440 · Postgres :5432 ·     │
│                                                Mongo :27017                            │
│  scripts/upload_files.py ──(OneLake DFS API)──┐            ▲                           │
│                                               │            │ on-premises data gateway  │
└───────────────────────────────────────────────┼────────────┼───────────────────────────┘
                                                ▼            │
┌──────────────────────────── FABRIC WORKSPACE fruitjuice-{dev|prod} ─────────────────────┐
│                                                                                          │
│  pl_fruitjuice_e2e (Data Pipeline)                                                       │
│   ├─ ForEach source ∈ {sqlserver, postgresql, mongodb}:                                  │
│   │     ForEach table: Copy activity (gateway) ──► lh_bronze/Files/landing/<src>/<tbl>/  │
│   │                    WHERE DATA_FATO > watermark   (Parquet; Mongo → JSON)              │
│   ├─ nb_bronze_load      Files/{landing/<db src>, upload/<csv|parquet|json>} ──►          │
│   │                      lh_bronze.brz_<src>_<tbl>  (native shape + _source,             │
│   │                      _ingested_at, _batch_id) ; ctl_watermark                         │
│   ├─ nb_silver_adapt     per-source adapter ──► lh_silver.stg_<src>_<tbl>                 │
│   │                      (canonical schema, DECIMAL(18,2), string codes)                  │
│   ├─ nb_silver_converge  6× stg_<src>_<tbl> == reference(parquet)?  ── FAIL ─► pipeline   │
│   │                      PASS ─► MERGE into lh_silver.<tbl>  (canonical, published)  stops │
│   ├─ wh_gold.gold.usp_load_all   (T-SQL, reads lh_silver.dbo.<tbl> by 3-part name)         │
│   │                      dim_* (SCD1, org path flattened) + fact_* ; usp_reconcile         │
│   └─ (Direct Lake reframes automatically)                                                │
│                                                                                          │
│  sm_fruitjuice (Direct Lake on wh_gold, DAX measures, RLS role "Region")                 │
│  rpt_fruitjuice (PBIR report)                                                            │
└──────────────────────────────────────────────────────────────────────────────────────────┘
        ▲ fabric-cicd (GitHub Actions, SPN) deploys main ─► fruitjuice-dev
        └ Fabric deployment pipeline  fruitjuice-dev ─► fruitjuice-prod (deployment rules)
          Feature work: branch-out workspaces (Git-connected) ─► PR ─► main
```

---

## Components

| Component | Purpose | Technology |
|-----------|---------|------------|
| `sources/` | Seeded generator for all 6 source shapes + local DBs | Python 3.11+, pyarrow, Docker Compose (done) |
| `scripts/upload_files.py` | Upload `csv`/`parquet`/`json` outputs to `lh_bronze/Files/upload/<fmt>/` | `azure-storage-file-datalake` against `onelake.dfs.fabric.microsoft.com`, `azure-identity` |
| `scripts/run_pipeline.py` | Trigger `pl_fruitjuice_e2e` and poll until done | Fabric REST jobs API |
| `scripts/deploy.py` | Publish `workspace/` to a target workspace | `fabric-cicd` (`publish_all_items`, `unpublish_all_orphan_items`) |
| `scripts/generate_partial.py` | Generate a dataset truncated at a date (incremental test, AT-007) | `sources` generator Python API |
| On-premises data gateway + 3 connections | Reach SQL Server, PostgreSQL, MongoDB in Docker | Manual one-off setup (not Git items) |
| `lh_bronze` | Raw landing (Files) + native-shape Delta tables + `ctl_watermark` | Fabric Lakehouse |
| `lh_silver` | Per-source canonical staging `stg_<src>_<tbl>` + published canonical `<tbl>` + `ctl_convergence_log` | Fabric Lakehouse |
| `wh_gold` | Star schema (`gold` schema), load + reconcile procedures | Fabric Warehouse (SQL database project in Git) |
| `nb_lib_contract` | Single source of truth in Fabric: tables, PKs, canonical schema, sources | Notebook (pure Python, `%run` target) |
| `nb_lib_adapters` | Pure DataFrame functions: one adapter per source + convergence helpers | Notebook (pure PySpark, `%run` target, unit-tested locally) |
| `nb_bronze_load`, `nb_silver_adapt`, `nb_silver_converge` | Orchestrated steps; thin wrappers around the libraries | Fabric Spark notebooks (Runtime 1.3: Spark 3.5, Delta 3.2, Python 3.11) |
| `pl_fruitjuice_e2e` | Orchestration: copies → notebooks → procs | Data Pipeline |
| `sm_fruitjuice` | Semantic model: relationships, measures, RLS role | Direct Lake, TMDL |
| `rpt_fruitjuice` | One report: revenue, margin, freight, actual vs target, by region/product/time | Power BI, PBIR |
| `tests/` | Local unit tests (contract, adapters, convergence) + e2e (counts, reconciliation, RLS) | pytest, pyspark 3.5, pyodbc |
| `.github/workflows/` | CI on PR (lint + local tests); deploy on merge to `main` | GitHub Actions |

---

## Key Decisions

### Decision 1: Land all database sources as files first, then one generic Bronze loader

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-10-02 |

**Context:** Six sources arrive two ways: Copy activity for the three databases and file upload for three file formats. Copy activity can write straight into Lakehouse tables, but Mongo documents are nested, and direct table sinks hide the native shape behind Copy's type mapping. The Bronze contract needs lineage columns on every row.

**Choice:** Copy activity writes **files** to `lh_bronze/Files/landing/<source>/<table>/<batch_id>/`: Parquet for SQL Server/Postgres (types preserved), JSON for Mongo (nesting preserved). The uploaded `csv`/`parquet`/`json` files go to `Files/upload/<fmt>/`. One notebook, `nb_bronze_load`, turns any of the 6 into `brz_<source>_<table>` Delta tables and adds `_source`, `_ingested_at` and `_batch_id`.

**Rationale:** One code path for lineage, watermarking and row counting across all sources. The native shape is kept exactly: Mongo stays nested and Postgres stays lower-case. The loader is a notebook, so it is reviewable in Git.

**Alternatives Rejected:**
1. Copy activity → `LakehouseTableSink` directly. Rejected: there's no place to add lineage columns uniformly, and the Mongo hierarchy would need per-collection mapping JSON.
2. OneLake shortcuts over the uploaded files. Rejected: the files are uploaded, so there is no external store to shortcut to.

**Consequences:**
- An extra hop (files → Delta) costs a few minutes. That's acceptable within the 60-minute SLA.
- Bronze for Mongo has **13** tables (12 flat collections + `sales`), so Bronze totals **73** tables, not 72. The DEFINE criterion is updated in its revision history.

---

### Decision 2: Convergence by staged comparison against a reference source, before publishing

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-10-02 |

**Context:** The core requirement (G2, AT-002) is that all six sources produce identical canonical data, and that drift **blocks** Gold.

**Choice:** `nb_silver_adapt` writes each source's canonical output to `lh_silver.stg_<source>_<table>` (full overwrite per run). `nb_silver_converge` takes **Parquet** as the reference: it's the generator's typed output and the reconciliation baseline. For each table and each other source it computes `exceptAll` in both directions and checks the counts. Any non-zero difference writes a row to `ctl_convergence_log` with sample keys and raises, which fails the pipeline before Gold. On a pass, the reference `stg_parquet_<table>` is MERGEd into the published `lh_silver.<table>` on its primary key.

**Rationale:** `exceptAll` on canonical, typed DataFrames is exact: it catches value, key and duplicate drift. ~3.4M fact rows × 6 sources is small for Spark. Staging keeps the published tables untouched on failure.

**Alternatives Rejected:**
1. Compare aggregates only (counts/sums). Rejected: offsetting errors pass, and a single-row drift in a key column would go undetected.
2. Row hashes (`sha2(concat_ws(...))`). Rejected for the decision itself: `exceptAll` is simpler and exact. Hashes remain an optimisation if runtime becomes a problem.
3. Merge sources by union and dedup. Rejected: it hides drift instead of detecting it.

**Consequences:**
- Convergence compares full staging tables every run (not only the new batch). That's simple and robust at this volume.
- The canonical type for every measure is `DECIMAL(18,2)`. Casting the same double to decimal yields the same value in every source; CSV text casts exactly. A unit test proves this (A-004).

---

### Decision 3: Gold in a Warehouse, built by stored procedures reading Silver through three-part names

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-10-02 |

**Context:** Approach B (chosen in brainstorm) puts Gold in a Fabric Warehouse. Warehouse source control stores the Warehouse as a SQL database project (preview). Gold must read `lh_silver` tables.

**Choice:** `wh_gold` contains schema `gold`: `dim_*`/`fact_*` tables, `gold.usp_load_dimensions`, `gold.usp_load_facts @from_date`, `gold.usp_load_all`, `gold.usp_reconcile`. Procedures read `lh_silver.dbo.<table>` by three-part name, which works across items in the same workspace. The pipeline calls `usp_load_all` through a Stored procedure activity.

**Rationale:** It covers DP-600 Warehouse skills (procs, CTAS/INSERT-SELECT, schemas). Cross-item reads avoid copying Silver. Procedures deploy with the Warehouse item via `fabric-cicd`.

**Alternatives Rejected:**
1. Gold in `lh_gold` with Spark (Approach A). Rejected by the user in brainstorm.
2. COPY INTO from OneLake files. Rejected: it's preview and would bypass Silver's Delta tables.

**Consequences:**
- ⚠ **Build risk (R-1):** a SQL project containing three-part references to a Lakehouse SQL endpoint may fail to build/publish without a database reference. **Fallback:** keep only tables in the Warehouse item. Keep the procedure bodies as `warehouse/gold/*.sql` scripts, run by a pipeline Script activity (same T-SQL, different home). Build must try the primary path first and record the outcome in BUILD_REPORT.
- Fabric Warehouse `MERGE` isn't relied on. Facts use delete-range + insert (idempotent by `@from_date`); dimensions use truncate + insert (small, SCD1).

---

### Decision 4: Promotion = Git branch-out for authoring, `fabric-cicd` for dev, deployment pipeline for prod

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-10-02 |

**Context:** The user kept both `fabric-cicd` and Fabric deployment pipelines (exam coverage). Two tools must not fight over the same workspace (A-008). The lab must rebuild from Git after trial expiry (G6).

**Choice:**
- Authoring happens in **branch-out feature workspaces** (Git integration, GitHub), opened as PRs to `main`.
- On merge to `main`, GitHub Actions runs `scripts/deploy.py` (`fabric-cicd`, SPN) → **`fruitjuice-dev`**, which is *not* Git-connected.
- The **Fabric deployment pipeline** (2 stages) promotes `fruitjuice-dev` → `fruitjuice-prod`, with deployment rules for the semantic model's data source and the notebooks' default lakehouse.
- A rebuild is the same `deploy.py` run against any empty workspace.

**Rationale:** Each tool owns exactly one target. `fabric-cicd`'s `parameter.yml` handles dev IDs. Deployment pipelines auto-bind items deployed together and their rules handle prod.

**Alternatives Rejected:**
1. `fruitjuice-dev` Git-connected and updated by `fabric-cicd`. Rejected: publishing into a Git-connected workspace leaves it permanently "uncommitted".
2. `fabric-cicd` for both dev and prod. Rejected: drops the deployment-pipeline exam coverage the user kept.

**Consequences:**
- Prod is only reachable through the deployment pipeline (manual promote). That's an acceptable lab gate.
- `parameter.yml` only needs a `DEV` environment (plus any rebuild target).

---

### Decision 5: Shared notebook libraries via `%run`, unit-tested locally by executing the notebook source

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-10-02 |

**Context:** The adapters are the heart of the project and must be unit-tested locally and in CI (G10). Fabric notebooks in Git are `notebook-content.py` files; magics appear as `# MAGIC` comment lines, so the file stays valid Python.

**Choice:** Pure functions live in `nb_lib_contract` and `nb_lib_adapters`: imports and `def`s only, no Spark actions at top level. Step notebooks `%run` them. Local tests load them with `runpy.run_path(".../notebook-content.py")` and call the functions with a local `SparkSession`. A contract test asserts `nb_lib_contract`'s schema equals `sources/src/generator` schemas (drift guard).

**Rationale:** No wheel build, no Environment item, no publish latency. The same file runs in Fabric and in pytest.

**Alternatives Rejected:**
1. A Python wheel in a Fabric Environment. Rejected: slow publish, extra item, and the Environment's custom-library sessions start slower on trial.
2. Logic inline in step notebooks. Rejected: untestable.

**Consequences:**
- Library notebooks must stay free of `display()` and of Spark actions at import time.
- Local Spark needs Java 17. CI uses `ubuntu-latest` + `actions/setup-java`; Windows contributors need a JDK (documented).

---

### Decision 6: RLS in the semantic model, not in the Warehouse

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-10-02 |

**Context:** G9 requires RLS by region. Warehouse RLS (security policies) forces Direct Lake to fall back to DirectQuery.

**Choice:** A semantic-model role `Region` with a DAX filter on `dim_cliente[regiao]`. The demo uses a static role per region (`Sudeste`, …); the test user is assigned via the workspace/semantic model security UI.

**Rationale:** It keeps Direct Lake in direct mode and is the DP-600 RLS objective.

**Alternatives Rejected:**
1. Warehouse security policy. Rejected: DirectQuery fallback.
2. Dynamic RLS with a `USERPRINCIPALNAME()` mapping table. Rejected (YAGNI): there's one test user.

**Consequences:**
- RLS protects the model only; direct SQL endpoint access is unrestricted. Acceptable, since Gold readers are workspace members.

---

### Decision 7: Watermarking at Bronze, PK MERGE at Silver, range reload at Gold

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-10-02 |

**Context:** G11 needs incremental facts; AT-006 needs idempotent reruns; AT-007 needs extension. The source facts are append-only by `DATA_FATO` (extending the generator's period never changes earlier data, a property tested in `sources/tests`).

**Choice:**
- **Bronze:** `ctl_watermark(source, table_name, max_data_fato)`. DB copies filter `WHERE DATA_FATO > @wm` in the source query. File sources filter in `nb_bronze_load`. Dimensions always reload in full (overwrite).
- **Silver:** staging rebuilt from all Bronze rows of the source each run; published tables MERGE on PK.
- **Gold:** `usp_load_facts @from_date` deletes `fact_*` rows with `date_key >= @from_date` and reinserts from Silver. `@from_date` = the minimum new `DATA_FATO` in this batch, or `9999-12-31` when there are none.
- Watermarks advance **only after Gold succeeds** (last pipeline activity).

**Rationale:** A rerun with no new rows touches nothing. A failed run leaves watermarks unchanged, so the next run retries the same window.

**Alternatives Rejected:**
1. Full reload every run. Rejected: misses G11.
2. CDC. Rejected: the sources are append-only, so CDC is unnecessary.

**Consequences:**
- Bronze can contain the same rows twice if a run fails after Bronze and is retried. Silver dedups via MERGE on PK (staging uses `dropDuplicates(pk)` over Bronze ordered by `_ingested_at` desc).

---

## File Manifest

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 1 | `pyproject.toml` (root) | Create | Lab tooling deps: pyspark 3.5.*, pytest, ruff, fabric-cicd, azure-identity, azure-storage-file-datalake, pyodbc; Python `>=3.11,<3.12` (matches Fabric Runtime 1.3) | @python-developer | None |
| 2 | `.gitignore` (root) | Create | `.venv/`, `.env`, `sources/data/`, `__pycache__/` | (general) | None |
| 3 | `.env.example` (root) | Create | `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `FABRIC_WORKSPACE_ID`, `FABRIC_ENV` | (general) | None |
| 4 | `workspace/lh_bronze.Lakehouse/{.platform,lakehouse.metadata.json}` | Create | Bronze lakehouse item | @fabric-architect | None |
| 5 | `workspace/lh_silver.Lakehouse/{.platform,lakehouse.metadata.json}` | Create | Silver lakehouse item | @fabric-architect | None |
| 6 | `workspace/nb_lib_contract.Notebook/notebook-content.py` (+ `.platform`) | Create | SOURCES, TABLES, PRIMARY_KEYS, DERIVED keys, CANONICAL_SCHEMAS (Spark StructTypes), MEASURES | @spark-engineer | None |
| 7 | `workspace/nb_lib_adapters.Notebook/notebook-content.py` (+ `.platform`) | Create | `adapt_csv/parquet/json/sqlserver/postgresql/mongodb`, `to_canonical`, `unnest_mongo_sales`, `diff_counts`, `with_lineage` | @spark-engineer | 6 |
| 8 | `workspace/nb_bronze_load.Notebook/notebook-content.py` (+ `.platform`) | Create | Read landing/upload files per source → `brz_<src>_<tbl>`; watermark filter; row-count output | @spark-engineer | 6, 7 |
| 9 | `workspace/nb_silver_adapt.Notebook/notebook-content.py` (+ `.platform`) | Create | For each source: Bronze → adapter → `stg_<src>_<tbl>` (+ DQ: PK unique/not null, FK, measures ≥ 0) | @spark-engineer | 6, 7, 8 |
| 10 | `workspace/nb_silver_converge.Notebook/notebook-content.py` (+ `.platform`) | Create | `exceptAll` vs reference; log to `ctl_convergence_log`; raise on drift; MERGE publish; output `from_date` | @spark-engineer | 6, 7, 9 |
| 11 | `workspace/nb_watermark_commit.Notebook/notebook-content.py` (+ `.platform`) | Create | Advance `ctl_watermark` after Gold succeeds | @spark-engineer | 6 |
| 12 | `workspace/wh_gold.Warehouse/` (SQL project: `wh_gold.sqlproj`, `gold/Tables/*.sql`) | Create | Gold tables: `dim_tempo`, `dim_cliente`, `dim_produto` (brand + category folded in), `dim_fabrica`, `dim_organizacional` (flattened path), `fact_vendas`, `fact_frete`, `fact_custo_fixo`, `fact_meta_faturamento`, `fact_meta_custo` | @sql-server-specialist | None |
| 13 | `workspace/wh_gold.Warehouse/gold/StoredProcedures/{usp_load_dimensions,usp_load_facts,usp_load_all,usp_reconcile}.sql` | Create | Load Gold from `lh_silver.dbo.*`; reconcile Gold vs Silver totals, `THROW` on mismatch | @sql-server-specialist | 12, 10 |
| 14 | `workspace/pl_fruitjuice_e2e.DataPipeline/pipeline-content.json` (+ `.platform`) | Create | Orchestration (see DAG); params `batch_id`, `full_reload`; connection IDs as placeholders | @fabric-pipeline-developer | 8–11, 13 |
| 15 | `workspace/sm_fruitjuice.SemanticModel/definition/{model.tmdl,tables/*.tmdl,relationships.tmdl,roles/Region.tmdl,expressions.tmdl}` + `definition.pbism` | Create | Direct Lake model on `wh_gold`; measures; RLS role | @fabric-architect | 12 |
| 16 | `workspace/rpt_fruitjuice.Report/definition.pbir` + `definition/` | Create | One report: KPI cards, revenue/margin trend, actual vs target, freight %, by region/category | @fabric-architect | 15 |
| 17 | `workspace/parameter.yml` | Create | `find_replace` for gateway connection IDs and lakehouse/warehouse IDs (`$items.Lakehouse.lh_silver.$id`, `$items.Warehouse.wh_gold.$sqlendpoint`) | @fabric-cicd-specialist | 4–16 |
| 18 | `scripts/deploy.py` | Create | `FabricWorkspace(...)`, `publish_all_items`, `unpublish_all_orphan_items`; SPN via `ClientSecretCredential` | @fabric-cicd-specialist | 17 |
| 19 | `scripts/upload_files.py` | Create | Upload `sources/data/{csv,parquet,json}/fruit_juice` → `lh_bronze/Files/upload/<fmt>/`; replace semantics | @python-developer | 1 |
| 20 | `scripts/run_pipeline.py` | Create | Start `pl_fruitjuice_e2e` (jobs API), poll status, exit non-zero on failure | @python-developer | 1 |
| 21 | `scripts/generate_partial.py` | Create | `generate(load_config(**{"period.end": D}))` into `sources/data` for AT-007 | @python-developer | None |
| 22 | `scripts/e2e.sh` | Create | generate → load → upload → run_pipeline → pytest `tests/e2e` | @shell-script-specialist | 18–21 |
| 23 | `tests/conftest.py` | Create | Session-scoped local `SparkSession` (local[2], UTC, ANSI off), `load_notebook()` helper (runpy), tiny generated dataset fixture (2013-01..2013-03, all 6 formats via `sources` API) | @test-generator | 6, 7 |
| 24 | `tests/test_contract.py` | Create | `nb_lib_contract` schemas/PKs == `sources/src/generator` schemas/PKs | @test-generator | 6, 23 |
| 25 | `tests/test_adapters.py` | Create | AT-003/004/005: each adapter yields the canonical schema; leading zeros; Mongo unnest == parquet 001/002; Postgres casing; DECIMAL cast equality (A-004) | @test-generator | 7, 23 |
| 26 | `tests/test_convergence.py` | Create | AT-002 (local): six fixture sources converge; a mutated row is detected with source/table/key | @test-generator | 7, 23 |
| 27 | `tests/e2e/test_bronze_counts.py` | Create | AT-001: 73 Bronze tables; counts == local generated counts (via SQL endpoint) | @test-generator | 20 |
| 28 | `tests/e2e/test_reconciliation.py` | Create | AT-001/006/007: Gold per-year totals of 9 measures == local Parquet totals to 0.00; `FATURAMENTO` = 2,664,425,394.54 | @test-generator | 20 |
| 29 | `tests/e2e/conftest.py` | Create | pyodbc connection to `wh_gold` / `lh_bronze` SQL endpoints with SPN access token | @test-generator | 1 |
| 30 | `.github/workflows/ci.yml` | Create | On PR: `uv sync`, ruff, `pytest tests -m "not e2e"` (Java 17), `sources` tests | @fabric-cicd-specialist | 23–26 |
| 31 | `.github/workflows/deploy-dev.yml` | Create | On push to `main` (+ manual dispatch with `workspace_id` input for rebuild): `scripts/deploy.py` with SPN secrets | @fabric-cicd-specialist | 18 |
| 32 | `docs/setup.md` | Create | One-off manual setup: trial capacity, tenant settings (SPN APIs), gateway install, 3 connections, workspaces, deployment pipeline + rules, RLS test user | @code-documenter | 14–18 |
| 33 | `README.md` | Modify | Update status, layout (`sources/` in-repo), quickstart, architecture link | @code-documenter | 32 |

**Total Files:** 33 entries (≈ 55 physical files including `.platform` and TMDL table files)

---

## Agent Assignment Rationale

| Agent | Files Assigned | Why This Agent |
|-------|----------------|----------------|
| @spark-engineer | 6–11 | PySpark DataFrame transforms, Delta MERGE, `exceptAll` convergence |
| @sql-server-specialist | 12, 13 | T-SQL procedures, set-based loads, three-part-name queries (Fabric Warehouse is T-SQL surface area) |
| @fabric-pipeline-developer | 14 | Copy activity with gateway connections, ForEach, Stored procedure activity, pipeline parameters |
| @fabric-architect | 4, 5, 15, 16 | Item definitions, Direct Lake model, report structure |
| @fabric-cicd-specialist | 17, 18, 30, 31 | `fabric-cicd`, `parameter.yml`, GitHub Actions |
| @python-developer | 1, 19–21 | Typed CLI scripts, Azure SDK clients |
| @shell-script-specialist | 22 | End-to-end orchestration script |
| @test-generator | 23–29 | pytest fixtures, local Spark, e2e tests |
| @code-documenter | 32, 33 | Setup guide and README |
| (general) | 2, 3 | Boilerplate |

**Agent Discovery:**
- Scanned: agent types available in this session (`.claude/agents/**/*.md` + plugin agents)
- Matched by: file type (`.py` notebook → spark, `.sql` → sql-server, `pipeline-content.json` → pipeline), purpose keywords, KB domains

---

## Code Patterns

### Pattern 1: Contract library notebook (`nb_lib_contract`)

```python
# Fabric notebook source

# CELL ********************

from pyspark.sql.types import (DateType, DecimalType, IntegerType, StringType, StructField,
                               StructType, TimestampType)

SOURCES = ["parquet", "csv", "json", "sqlserver", "postgresql", "mongodb"]
REFERENCE_SOURCE = "parquet"
DB_SOURCES = ["sqlserver", "postgresql", "mongodb"]
DIMENSIONS = ["dim_categoria", "dim_marca", "dim_produto", "dim_cliente", "dim_fabrica",
              "dim_organizacional", "dim_tempo"]
FACTS = ["tab_fato001", "tab_fato002", "tab_fato003", "tab_fato004", "tab_fato005"]
TABLES = DIMENSIONS + FACTS

S, D, I = StringType(), DecimalType(18, 2), IntegerType()

def _facts(*cols):
    base = [("DATA_FATO", TimestampType()), ("COD_DIA", S)]
    return StructType([StructField(n, t, False) for n, t in base + list(cols)])

CANONICAL_SCHEMAS = {
    "tab_fato001": _facts(("COD_CLIENTE", S), ("COD_FABRICA", S), ("COD_PRODUTO", S),
                          ("COD_ORGANIZACIONAL", S), ("FATURAMENTO", D), ("IMPOSTO", D),
                          ("CUSTO_VARIAVEL", D), ("UNIDADE_VENDIDA", D), ("QUANTIDADE_VENDIDA", D)),
    "tab_fato002": _facts(("COD_CLIENTE", S), ("COD_FABRICA", S), ("COD_PRODUTO", S), ("FRETE", D)),
    "tab_fato003": _facts(("COD_FABRICA", S), ("CUSTO_FIXO", D)),
    "tab_fato004": _facts(("COD_CLIENTE", S), ("COD_PRODUTO", S), ("COD_ORGANIZACIONAL", S),
                          ("META_FATURAMENTO", D)),
    "tab_fato005": _facts(("COD_FABRICA", S), ("COD_PRODUTO", S), ("META_CUSTO", D)),
    # dimensions: same names/types as sources/src/generator/dims.py + timedim.py (strings, INT for
    # Esquerda/Direita/Nivel and dim_tempo Cod_Semana/Cod_Mes/Cod_Trimestre/Cod_Semestre, DATE for Data)
}

PRIMARY_KEYS = {
    "tab_fato001": ["COD_CLIENTE", "COD_PRODUTO", "COD_ORGANIZACIONAL", "COD_FABRICA", "COD_DIA"],
    "tab_fato002": ["COD_CLIENTE", "COD_PRODUTO", "COD_FABRICA", "COD_DIA"],
    "tab_fato003": ["COD_FABRICA", "COD_DIA"],
    "tab_fato004": ["COD_CLIENTE", "COD_PRODUTO", "COD_ORGANIZACIONAL", "COD_DIA"],
    "tab_fato005": ["COD_PRODUTO", "COD_FABRICA", "COD_DIA"],
    "dim_categoria": ["Cod_Categoria"], "dim_marca": ["Cod_Marca"], "dim_produto": ["Cod_Produto"],
    "dim_cliente": ["Cod_Cliente"], "dim_fabrica": ["Cod_Fabrica"],
    "dim_organizacional": ["Cod_Filho"], "dim_tempo": ["Cod_Dia"],
}
```

### Pattern 2: Adapters, one per source, all ending in `to_canonical` (`nb_lib_adapters`)

```python
from pyspark.sql import DataFrame, functions as F

def to_canonical(df: DataFrame, table: str) -> DataFrame:
    """Select + cast to the canonical schema; names matched case-insensitively."""
    by_lower = {c.lower(): c for c in df.columns}
    cols = []
    for field in CANONICAL_SCHEMAS[table].fields:
        src = by_lower[field.name.lower()]  # KeyError = contract violation, fail loudly
        cols.append(F.col(f"`{src}`").cast(field.dataType).alias(field.name))
    return df.select(*cols)

def adapt_csv(df: DataFrame, table: str) -> DataFrame:
    # Bronze CSV is read with inferSchema=false: every column is a string, so codes keep zeros
    return to_canonical(df, table)

def adapt_postgresql(df: DataFrame, table: str) -> DataFrame:
    # identifiers arrive lower-cased (cod_fabrica); to_canonical maps them back by name
    return to_canonical(df, table)

def adapt_json(df: DataFrame, table: str) -> DataFrame:
    if "DATA_FATO" in df.columns:  # ISO-8601 string "2013-01-01T00:00:00.000"
        df = df.withColumn("DATA_FATO", F.to_timestamp("DATA_FATO"))
    return to_canonical(df, table)

def _unwrap_mongo_date(col: str):
    # Copy activity JSON keeps Extended JSON: {"$date": "..."} -> struct<$date: string>
    return F.to_timestamp(F.col(f"`{col}`.`$date`"))

def unnest_mongo_sales(sales: DataFrame) -> dict[str, DataFrame]:
    """Mongo `sales` (one denormalised doc per fato001 row) -> canonical tab_fato001 + tab_fato002."""
    flat = sales.select(
        _unwrap_mongo_date("data").alias("DATA_FATO"), F.col("cod_dia").alias("COD_DIA"),
        F.col("cliente.cod").alias("COD_CLIENTE"), F.col("produto.cod").alias("COD_PRODUTO"),
        F.col("fabrica.cod").alias("COD_FABRICA"), F.col("organizacional.cod").alias("COD_ORGANIZACIONAL"),
        F.col("faturamento").alias("FATURAMENTO"), F.col("imposto").alias("IMPOSTO"),
        F.col("custo_variavel").alias("CUSTO_VARIAVEL"), F.col("unidades").alias("UNIDADE_VENDIDA"),
        F.col("quantidade").alias("QUANTIDADE_VENDIDA"), F.col("frete").alias("FRETE"))
    return {"tab_fato001": to_canonical(flat, "tab_fato001"),
            "tab_fato002": to_canonical(flat, "tab_fato002")}

def adapt_mongodb(df: DataFrame, table: str) -> DataFrame:
    if "DATA_FATO" in df.columns:
        df = df.withColumn("DATA_FATO", _unwrap_mongo_date("DATA_FATO"))
    return to_canonical(df.drop("_id"), table)

ADAPTERS = {"csv": adapt_csv, "parquet": to_canonical, "json": adapt_json,
            "sqlserver": to_canonical, "postgresql": adapt_postgresql, "mongodb": adapt_mongodb}
# nb_silver_adapt: for mongodb, tab_fato001/002 come from unnest_mongo_sales(brz_mongodb_sales);
# the flat brz_mongodb_tab_fato001/002 are still loaded to Bronze but not used for Silver.
```

### Pattern 3: Convergence check (`nb_lib_adapters`, used by `nb_silver_converge`)

```python
def diff_counts(reference: DataFrame, candidate: DataFrame) -> tuple[int, int, DataFrame]:
    """Exact multiset difference in both directions. (0, 0) means identical."""
    missing = reference.exceptAll(candidate)   # in reference, not in candidate
    extra = candidate.exceptAll(reference)     # in candidate, not in reference
    return missing.count(), extra.count(), extra.unionByName(missing).limit(5)

# nb_silver_converge (step notebook)
failures = []
for table in TABLES:
    ref = spark.table(f"stg_{REFERENCE_SOURCE}_{table}")
    for source in SOURCES:
        if source == REFERENCE_SOURCE:
            continue
        missing, extra, sample = diff_counts(ref, spark.table(f"stg_{source}_{table}"))
        log_convergence(batch_id, table, source, missing, extra, sample)  # appends ctl_convergence_log
        if missing or extra:
            failures.append(f"{source}.{table}: missing={missing} extra={extra}")
if failures:
    raise RuntimeError("Convergence failed - Gold not published:\n" + "\n".join(failures))
```

### Pattern 4: Bronze loader with lineage + watermark (`nb_bronze_load`)

```python
READERS = {
    "csv":     lambda p: spark.read.option("header", True).option("inferSchema", False).csv(p),
    "parquet": lambda p: spark.read.parquet(p),          # hive year=YYYY partitions -> `year` column
    "json":    lambda p: spark.read.json(p),
    "sqlserver": lambda p: spark.read.parquet(p),        # Copy activity landing, Parquet
    "postgresql": lambda p: spark.read.parquet(p),
    "mongodb": lambda p: spark.read.json(p),             # Copy activity landing, JSON (nested kept)
}

def source_path(source: str, table: str) -> str:
    if source in DB_SOURCES:
        return f"Files/landing/{source}/{table}/{batch_id}/"
    ext = {"csv": f"{table}.csv", "json": f"{table}.jsonl", "parquet": table}[source]
    return f"Files/upload/{source}/{ext}"

def load(source: str, table: str) -> int:
    df = READERS[source](source_path(source, table))
    if table in FACTS and source not in DB_SOURCES:      # DB sources are already filtered by Copy
        wm = read_watermark(source, table)               # None on first run
        if wm is not None:
            df = df.where(F.to_timestamp(_fact_date_col(df, source)) > F.lit(wm))
    df = with_lineage(df, source, batch_id)              # _source, _ingested_at, _batch_id
    mode = "append" if table in FACTS else "overwrite"
    df.write.format("delta").mode(mode).option("mergeSchema", "true").saveAsTable(f"brz_{source}_{table}")
    return df.count()
```

### Pattern 5: Gold fact load, idempotent by range (`gold.usp_load_facts`)

```sql
CREATE PROCEDURE gold.usp_load_facts @from_date DATE
AS
BEGIN
    DELETE FROM gold.fact_vendas WHERE data >= @from_date;

    INSERT INTO gold.fact_vendas
        (data, cod_dia, cod_cliente, cod_produto, cod_fabrica, cod_organizacional,
         faturamento, imposto, custo_variavel, unidade_vendida, quantidade_vendida)
    SELECT CAST(f.DATA_FATO AS DATE), f.COD_DIA, f.COD_CLIENTE, f.COD_PRODUTO, f.COD_FABRICA,
           f.COD_ORGANIZACIONAL, f.FATURAMENTO, f.IMPOSTO, f.CUSTO_VARIAVEL,
           f.UNIDADE_VENDIDA, f.QUANTIDADE_VENDIDA
    FROM lh_silver.dbo.tab_fato001 AS f
    WHERE CAST(f.DATA_FATO AS DATE) >= @from_date;

    -- same shape for fact_frete (002), fact_custo_fixo (003),
    -- fact_meta_faturamento (004), fact_meta_custo (005)
END;
```

```sql
CREATE PROCEDURE gold.usp_reconcile
AS
BEGIN
    DECLARE @diff INT = (
        SELECT COUNT(*) FROM (
            SELECT YEAR(DATA_FATO) AS ano, SUM(FATURAMENTO) AS v FROM lh_silver.dbo.tab_fato001 GROUP BY YEAR(DATA_FATO)
            EXCEPT
            SELECT YEAR(data), SUM(faturamento) FROM gold.fact_vendas GROUP BY YEAR(data)
        ) AS d);   -- repeated per measure/fact in the build
    IF @diff > 0
        THROW 50001, 'Gold does not reconcile with Silver (fact_vendas.faturamento)', 1;
END;
```

### Pattern 6: Org hierarchy flattening (`gold.usp_load_dimensions`)

```sql
-- dim_organizacional is a nested set (Esquerda/Direita/Nivel). Each node's ancestors are the
-- nodes whose interval contains it; pivot ancestors by level into path columns.
INSERT INTO gold.dim_organizacional (cod_org, desc_org, nivel, nivel1, nivel2, nivel3, nivel4)
SELECT n.Cod_Filho, n.Desc_Filho, n.Nivel,
       MAX(CASE WHEN a.Nivel = 1 THEN a.Desc_Filho END),
       MAX(CASE WHEN a.Nivel = 2 THEN a.Desc_Filho END),
       MAX(CASE WHEN a.Nivel = 3 THEN a.Desc_Filho END),
       MAX(CASE WHEN a.Nivel = 4 THEN a.Desc_Filho END)
FROM lh_silver.dbo.dim_organizacional AS n
JOIN lh_silver.dbo.dim_organizacional AS a
  ON a.Esquerda <= n.Esquerda AND a.Direita >= n.Direita
GROUP BY n.Cod_Filho, n.Desc_Filho, n.Nivel;
-- Build verifies the actual max Nivel in the data and sizes the path columns accordingly.
```

### Pattern 7: Configuration structure (`workspace/parameter.yml`) and deploy

```yaml
find_replace:
  # Gateway connection IDs are placeholders in pipeline-content.json
  - find_value: "00000000-0000-0000-0000-00000000c001"   # sqlserver connection placeholder
    replace_value:
      DEV: "<sqlserver-connection-guid>"
    item_type: "DataPipeline"
  - find_value: "00000000-0000-0000-0000-00000000c002"   # postgresql
    replace_value:
      DEV: "<postgresql-connection-guid>"
    item_type: "DataPipeline"
  - find_value: "00000000-0000-0000-0000-00000000c003"   # mongodb
    replace_value:
      DEV: "<mongodb-connection-guid>"
    item_type: "DataPipeline"
  # Lakehouse/warehouse IDs referenced by the pipeline and notebooks (feature-workspace IDs in Git)
  - find_value: "<lh_bronze-id-in-git>"
    replace_value:
      DEV: "$items.Lakehouse.lh_bronze.$id"
  - find_value: "<lh_silver-id-in-git>"
    replace_value:
      DEV: "$items.Lakehouse.lh_silver.$id"
  - find_value: "<wh_gold-sqlendpoint-in-git>"
    replace_value:
      DEV: "$items.Warehouse.wh_gold.$sqlendpoint"
    item_type: "SemanticModel"
```

```python
# scripts/deploy.py
import os

from azure.identity import ClientSecretCredential
from fabric_cicd import FabricWorkspace, publish_all_items, unpublish_all_orphan_items

ITEM_TYPES = ["Lakehouse", "Warehouse", "Notebook", "DataPipeline", "SemanticModel", "Report"]

def main() -> None:
    credential = ClientSecretCredential(os.environ["AZURE_TENANT_ID"], os.environ["AZURE_CLIENT_ID"],
                                        os.environ["AZURE_CLIENT_SECRET"])
    workspace = FabricWorkspace(
        workspace_id=os.environ["FABRIC_WORKSPACE_ID"],
        environment=os.environ.get("FABRIC_ENV", "DEV"),
        repository_directory="workspace",
        item_type_in_scope=ITEM_TYPES,
        token_credential=credential,
    )
    publish_all_items(workspace)
    unpublish_all_orphan_items(workspace)

if __name__ == "__main__":
    main()
```

---

## Data Flow

```text
1. Local: `uv run generator generate` (seed 42) → sources/data/<fmt>/fruit_juice; `generator load` → 3 DBs
   │
   ▼
2. Local: scripts/upload_files.py → lh_bronze/Files/upload/{csv,parquet,json}/
   │
   ▼
3. Fabric pl_fruitjuice_e2e (batch_id = pipeline RunId):
   3a. Lookup ctl_watermark → ForEach DB source × table: Copy (gateway) → Files/landing/<src>/<tbl>/<batch_id>/
   3b. nb_bronze_load → brz_<src>_<tbl> (73 tables; dims overwrite, facts append > watermark)
   3c. nb_silver_adapt → stg_<src>_<tbl> (canonical; DQ checks fail the run on PK/FK/negative violations)
   3d. nb_silver_converge → exceptAll vs stg_parquet_*; FAIL stops here; PASS MERGEs lh_silver.<tbl>,
       returns from_date (min new DATA_FATO, or 9999-12-31)
   3e. Stored procedure: wh_gold.gold.usp_load_all @from_date → dims (truncate/insert), facts (range reload)
   3f. Stored procedure: gold.usp_reconcile → THROW on any Gold≠Silver total
   3g. nb_watermark_commit → advance ctl_watermark
   │
   ▼
4. sm_fruitjuice (Direct Lake) reframes; rpt_fruitjuice shows the new data
   │
   ▼
5. Local: pytest tests/e2e → Bronze counts == generated counts; Gold totals == generated Parquet totals
```

---

## Integration Points

| External System | Integration Type | Authentication |
|-----------------|-----------------|----------------|
| SQL Server 2025 (Docker, 127.0.0.1:1440) | Copy activity via on-prem data gateway, SQL Server connector | SQL auth (`sa` / `.env`), stored in Fabric connection |
| PostgreSQL 17 (Docker, :5432) | Copy activity via gateway, PostgreSQL connector | Basic, stored in Fabric connection |
| MongoDB 8 (Docker, :27017) | Copy activity via gateway, MongoDB connector (source supports on-prem gateway, Basic auth: verified on Microsoft Learn) | Basic (`labsources` user, `authSource=admin`) |
| OneLake (`onelake.dfs.fabric.microsoft.com`) | ADLS Gen2 DFS API for file upload | Entra ID (user via `DefaultAzureCredential`, or SPN) |
| Fabric REST API | Job run/poll, `fabric-cicd` item CRUD | SPN (`ClientSecretCredential`), tenant setting "Service principals can use Fabric APIs" |
| Warehouse / Lakehouse SQL endpoints | e2e tests via pyodbc (ODBC Driver 18) | SPN access token (`SQL_COPT_SS_ACCESS_TOKEN`) |
| GitHub | Git integration (branch-out workspaces), Actions | GitHub app/PAT for Fabric Git; Actions secrets for SPN |

---

## Testing Strategy

| Test Type | Scope | Files | Tools | Coverage Goal |
|-----------|-------|-------|-------|---------------|
| Unit: contract | Fabric contract == generator schema/PKs | `tests/test_contract.py` | pytest | 12/12 tables |
| Unit: adapters | Each of 6 adapters → canonical schema on a 3-month fixture; Mongo unnest; casing; zeros; decimal equality | `tests/test_adapters.py` | pytest + pyspark 3.5 local | 6/6 sources × 12 tables |
| Unit: convergence | Clean fixture converges; mutated value, deleted row, duplicated row each detected and named | `tests/test_convergence.py` | pytest + pyspark | 3 drift kinds |
| Generator regression | Vendored generator still deterministic and valid | `sources/tests/` | pytest (existing, 16 tests) | Existing |
| E2E: Bronze | 73 tables, counts == generated | `tests/e2e/test_bronze_counts.py` | pytest + pyodbc (`-m e2e`) | 73/73 |
| E2E: reconciliation | Per-year totals of 9 measures == Parquet to 0.00; grand total 2,664,425,394.54 | `tests/e2e/test_reconciliation.py` | pytest + pyodbc + pyarrow | 5 facts × 14 years |
| E2E: incremental | Partial generate (end 2026-06-30) → run → full generate → run; second run after that inserts 0 | `scripts/e2e.sh --incremental` + reconciliation test | bash + pytest | AT-006, AT-007 |
| Manual | Deployment pipeline promote, RLS role view-as, report render, Purview lineage | `docs/setup.md` checklist | Fabric portal | AT-010, AT-011, G12 |

**Acceptance test coverage:**

| AT | Covered by |
|----|------------|
| AT-001 | e2e bronze counts + reconciliation after `scripts/e2e.sh` on empty dev |
| AT-002 | `test_convergence.py` (local) + manual drift in Postgres (`UPDATE ... SET FATURAMENTO = FATURAMENTO + 0.01` on one row) → pipeline fails at `nb_silver_converge`, `ctl_convergence_log` names `postgresql.tab_fato001` + key |
| AT-003 | `test_adapters.py::test_codes_keep_leading_zeros[*]` |
| AT-004 | `test_adapters.py::test_mongo_sales_unnests_to_parquet_001_002` |
| AT-005 | `test_adapters.py::test_postgres_lowercase_columns` |
| AT-006 | `scripts/e2e.sh` second run + reconciliation unchanged + Bronze fact counts unchanged |
| AT-007 | `scripts/e2e.sh --incremental` |
| AT-008 | Manual: stop the Postgres container → pipeline fails at Copy; Silver/Gold timestamps unchanged |
| AT-009 | `deploy-dev.yml` manual dispatch into an empty workspace + `scripts/e2e.sh` |
| AT-010 | Manual (deployment pipeline), checklist in `docs/setup.md` |
| AT-011 | Manual "View as role" + DAX query check against Gold per-region sum |
| AT-012 | `usp_reconcile` includes the org roll-up check (root total == fact grand total) |

---

## Error Handling

| Error Type | Handling Strategy | Retry? |
|------------|-------------------|--------|
| Gateway offline / DB container down | Copy activity fails → pipeline fails; no downstream activity runs (all `On success` dependencies) | Yes: Copy activity retry 2 × 60 s; then fail |
| Missing uploaded file | `nb_bronze_load` raises with the missing path before writing anything | No |
| Contract violation (missing column) | `to_canonical` raises `KeyError` with source/table/column | No |
| DQ violation in staging (null/dup PK, orphan FK, negative measure) | `nb_silver_adapt` raises with counts per rule; staging left for inspection | No |
| Convergence drift | Log to `ctl_convergence_log` with sample keys, raise; published Silver and Gold untouched | No |
| Gold reconcile mismatch | `usp_reconcile` `THROW 50001`; watermarks not advanced | No |
| Partial failure mid-run | Watermarks advance only in the last activity, so the next run reprocesses the window; Silver MERGE and Gold range reload are idempotent | Re-run pipeline |
| Capacity throttling (trial) | Activity-level retry 1 × 300 s on notebooks | Yes |

---

## Configuration

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| `batch_id` (pipeline param) | string | `@pipeline().RunId` | Lineage + landing folder |
| `full_reload` (pipeline param) | bool | `false` | Ignore watermarks; overwrite Bronze facts; `from_date = 1900-01-01` |
| `sources` (pipeline param) | array | all 6 | Subset of sources for debugging; convergence requires ≥ 2 incl. reference |
| `REFERENCE_SOURCE` (`nb_lib_contract`) | string | `parquet` | Baseline for convergence |
| `FABRIC_WORKSPACE_ID` (env) | string | none | Deploy/run target |
| `FABRIC_ENV` (env) | string | `DEV` | `parameter.yml` environment key |
| `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` (env / GH secrets) | string | none | SPN for deploy, run, e2e |
| Generator config | toml | `sources/src/config/fruit_juice.toml` | Seed 42, 2013-01-01..2026-12-31, all 6 formats |

---

## Security Considerations

- Secrets never live in Git: SPN credentials in GitHub Actions secrets and the local `.env` (git-ignored). DB credentials live only in Fabric connections and `sources/.env`.
- The SPN gets **Contributor** on `fruitjuice-dev` only, not Admin, and no prod role. Prod changes go through the deployment pipeline, run by the user.
- Docker ports stay bound to `127.0.0.1`; the gateway runs on the same host, so nothing is exposed to the network.
- OneLake security (COULD, G12): data access role on `lh_bronze` limited to an `engineers` group.
- RLS role `Region` on the semantic model (Decision 6). Test with "View as".
- No PII: the dataset's clients are fictional businesses.

---

## Observability

| Aspect | Implementation |
|--------|----------------|
| Logging | Each step notebook ends with `notebookutils.notebook.exit(json.dumps({...}))` (row counts per source/table, from_date). The pipeline surfaces this in the monitoring hub |
| Metrics | `lh_bronze.ctl_load_log` (batch, source, table, rows, duration) and `lh_silver.ctl_convergence_log` (batch, table, source, missing, extra, sample_keys) as Delta tables, queryable from SQL endpoints |
| Tracing | `_batch_id` = pipeline RunId on every Bronze row and every log row: one ID ties a run end to end |

---

## Pipeline Architecture

### DAG Diagram

```text
[Lookup watermarks]
      │
      ├──► [ForEach sqlserver tables:  Copy → Files/landing] ──┐
      ├──► [ForEach postgresql tables: Copy → Files/landing] ──┤   (parallel)
      └──► [ForEach mongodb colls:     Copy → Files/landing] ──┤
                                                              ▼
                                             [nb_bronze_load (6 sources)]
                                                              ▼
                                             [nb_silver_adapt (6 sources)]
                                                              ▼
                                             [nb_silver_converge]  ── fail ──► (stop, no Gold)
                                                              ▼
                                             [SP gold.usp_load_all @from_date]
                                                              ▼
                                             [SP gold.usp_reconcile]
                                                              ▼
                                             [nb_watermark_commit]
```

### Partition Strategy

| Table | Partition Key | Granularity | Rationale |
|-------|-------------|-------------|-----------|
| `brz_*`, `stg_*`, `lh_silver.*` | none | n/a | ≤ ~1M rows per table; partitioning would create tiny files. V-Order + `OPTIMIZE` after loads instead |
| Gold `fact_*` | none (Warehouse manages distribution) | n/a | Warehouse auto-manages storage |
| `Files/landing/<src>/<tbl>/<batch_id>/` | folder per batch | per run | Isolates each run's copy output; old batches can be deleted |

### Incremental Strategy

| Model | Strategy | Key Column | Lookback |
|-------|----------|------------|----------|
| Bronze facts (6 sources) | Watermark + append | `DATA_FATO` (Mongo `sales`: `data`) | 0 (strictly greater) |
| Bronze dims | Full overwrite | n/a | n/a |
| Silver staging | Full rebuild from Bronze, dedup on PK by latest `_ingested_at` | PK | All |
| Silver published | MERGE on PK | PK | n/a |
| Gold dims | Truncate + insert (SCD1) | n/a | n/a |
| Gold facts | Delete range + insert | `data >= @from_date` | from the batch's min new date |

### Schema Evolution Plan

| Change Type | Handling | Rollback |
|-------------|----------|----------|
| New column in a source | Bronze accepts it (`mergeSchema`); ignored by `to_canonical` until added to `nb_lib_contract` (+ contract test vs generator) | Remove from contract |
| Type change in a source | Absorbed by `to_canonical` casts; the convergence check proves values still agree | Revert cast |
| Column removal | `to_canonical` fails loudly (KeyError), so the contract must change deliberately | Re-add column |
| Canonical contract change | Change `nb_lib_contract` + Gold DDL + semantic model in one PR; `full_reload=true` run | Revert PR, full reload |

### Data Quality Gates

| Gate | Tool | Threshold | Action on Failure |
|------|------|-----------|-------------------|
| Bronze row count per source/table logged | `nb_bronze_load` → `ctl_load_log` | n/a (observed; asserted in e2e) | e2e test fails |
| PK not null + unique in staging | `nb_silver_adapt` | 0 | Block pipeline |
| FK integrity (facts → dims, `schema.py` FKs) | `nb_silver_adapt` (left-anti joins) | 0 orphans | Block pipeline |
| Measures ≥ 0, not null | `nb_silver_adapt` | 0 | Block pipeline |
| Derived-key invariants (002 = 001 keys, etc.) | `nb_silver_adapt` on the reference source | 0 differences | Block pipeline |
| Cross-source convergence | `nb_silver_converge` | 0 missing, 0 extra | Block pipeline (no Gold) |
| Gold ↔ Silver reconciliation | `gold.usp_reconcile` | 0 mismatched year-sums | Block (watermarks not advanced) |
| Gold ↔ generator reconciliation | `tests/e2e/test_reconciliation.py` | 0.00 delta | Test fails |

---

## Assumption Resolution (from DEFINE)

| ID | Status | Evidence / Plan |
|----|--------|-----------------|
| A-001 MongoDB via gateway | ✅ Resolved | Microsoft Learn MongoDB connector: Pipeline Copy activity source supports **On-premises** gateway, Basic auth |
| A-002 SPN APIs on trial tenant | Open → Build step 0 | `docs/setup.md` enables the tenant setting; if blocked, `deploy.py` falls back to `AzureCliCredential` (user) |
| A-003 Gateway reaches 127.0.0.1 ports | Open → Build step 0 | Same-host gateway; verify with "Test connection" on each Fabric connection |
| A-004 DECIMAL cast equality | Planned test | `test_adapters.py::test_decimal_cast_identical_across_sources` |
| A-005 Reference total | Planned test | e2e computes totals from the generated Parquet; 2,664,425,394.54 asserted as a sanity constant |
| A-006 60-minute SLA | Measured | Pipeline duration recorded in BUILD_REPORT |
| A-007 Direct Lake on Warehouse | Mitigated | No Warehouse RLS (Decision 6); star schema + V-Order; check fallback via DMV |
| A-008 cicd + deployment pipelines | ✅ Resolved by design | Decision 4: each tool owns one workspace |
| R-1 Warehouse SQL project with 3-part refs | New risk | Decision 3 fallback: procedure bodies as Script activities |

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-10-02 | design-agent | Initial version |

---

## Next Step

**Ready for:** `/build .claude/sdd/features/DESIGN_FABRIC_MEDALLION_LAKEHOUSE.md`
