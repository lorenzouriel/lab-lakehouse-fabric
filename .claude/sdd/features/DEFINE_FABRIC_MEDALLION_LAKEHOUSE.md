# DEFINE: Fabric Medallion Lakehouse (multi-source convergence)

> A reproducible Microsoft Fabric medallion lakehouse that ingests the Fruit Juice dataset from six differently shaped sources and proves they converge to one Silver layer and a Gold star schema that reconciles exactly to the source.

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | FABRIC_MEDALLION_LAKEHOUSE |
| **Date** | 2026-10-02 |
| **Author** | define-agent |
| **Status** | ✅ Complete (Designed) |
| **Clarity Score** | 14/15 |
| **Input** | `.claude/sdd/features/BRAINSTORM_FABRIC_MEDALLION_LAKEHOUSE.md` (brainstorm_document) |

---

## Problem Statement

The same Fruit Juice dataset exists in six shapes: CSV, Parquet, JSON Lines, SQL Server, PostgreSQL, and MongoDB with denormalised `sales` documents. Nothing yet shows, in a runnable and testable way, that a Fabric medallion lakehouse can integrate all six into one trustworthy analytical model. That leaves the author without hands-on DP-700 / DP-600 coverage and without a credible portfolio piece.

---

## Target Users

| User | Role | Pain Point |
|------|------|------------|
| The author | Data engineer preparing for DP-700 and DP-600 | Needs hands-on practice across Lakehouse, Warehouse, pipelines, gateway, Direct Lake, RLS, Purview, Git integration and deployment pipelines, on a trial capacity that expires |
| Portfolio reviewers | Hiring managers / peer engineers reading the repo | Can't judge multi-source integration skill from screenshots; need code, tests and a reproducible run |

---

## Goals

| Priority | Goal |
|----------|------|
| **MUST** | G1. Ingest all 12 tables from each of the 6 sources into `lh_bronze` in their native shape, with lineage columns (`_source`, `_ingested_at`, `_batch_id`) |
| **MUST** | G2. Silver: one adapter per source maps it to a single canonical schema; a convergence check proves the 6 canonical outputs are identical before anything is published to Gold |
| **MUST** | G3. Gold in a Fabric Warehouse (T-SQL): conformed dimensions (SCD1, flattened org hierarchy) + 5 fact tables, built from canonical Silver |
| **MUST** | G4. Gold reconciliation: row counts and measure totals per table and year equal the generator output |
| **MUST** | G5. Direct Lake semantic model on `wh_gold` with core measures (revenue, margin, freight, actual vs target) and one Power BI report |
| **MUST** | G6. Every Fabric item is defined in Git and deployable to an empty workspace with `fabric-cicd` (GitHub Actions, service principal), so the lab survives trial expiry |
| **MUST** | G7. One orchestrating Data Factory pipeline runs ingestion → Silver → convergence → Gold end to end |
| **SHOULD** | G8. Fabric deployment pipeline promotes `fruitjuice-dev` → `fruitjuice-prod`, with deployment rules re-pointing lakehouse/warehouse/connection IDs |
| **SHOULD** | G9. RLS by region (`Desc_Regiao`) in the semantic model, verified with a test role |
| **SHOULD** | G10. Silver adapters unit-tested locally with PySpark against a short generated period, run in CI on PRs |
| **SHOULD** | G11. Fact tables load incrementally by a `DATA_FATO` watermark; dimensions full-reload |
| **COULD** | G12. Purview scan shows lineage source → Bronze → Silver → Gold; OneLake security restricts `lh_bronze` to an engineers group |
| **COULD** | G13. Data-quality checks in Silver (PK uniqueness, FK integrity, non-negative measures, no nulls) reusing the rules in `sources/src/generator/schema.py` |

*`sources/` vendoring + trim (done during brainstorm, 16/16 tests passing) is a prerequisite, not a goal.*

---

## Success Criteria

- [ ] **Bronze completeness:** 73 Bronze tables (12 tables × 6 sources, plus Mongo `sales`). Each table's row count equals its source count. For example, `tab_fato001` = 1,050,428 rows from every source, including Mongo `sales`, which is 1,050,428 documents
- [ ] **Convergence:** for each of the 12 tables, the 6 canonical Silver outputs have equal row counts, an identical key set (0 rows in a symmetric anti-join), and identical per-row measure values: a 0-row `EXCEPT` between every source and the reference source
- [ ] **Reconciliation:** Gold `SUM(FATURAMENTO)` = **2,664,425,394.54** (seed 42), and per-year sums of all 9 measures (`FATURAMENTO`, `IMPOSTO`, `CUSTO_VARIAVEL`, `UNIDADE_VENDIDA`, `QUANTIDADE_VENDIDA`, `FRETE`, `CUSTO_FIXO`, `META_FATURAMENTO`, `META_CUSTO`) differ from the generated Parquet by **0.00**
- [ ] **Data quality:** 0 null primary keys, 0 duplicate primary keys, 0 orphan foreign keys across Silver and Gold
- [ ] **Rebuild:** an empty workspace reaches a fully deployed state from Git with **1** `fabric-cicd` run (plus one pipeline run to load data), and no manual item creation
- [ ] **End-to-end run:** the orchestrating pipeline completes all 6 sources → Gold in **≤ 60 minutes** on the trial capacity
- [ ] **Incremental:** a second pipeline run with no new source rows inserts **0** fact rows and leaves Gold totals unchanged (idempotent)
- [ ] **RLS:** a test role scoped to one region sees only that region's revenue, and its total equals the per-region Gold sum
- [ ] **Local tests:** adapter unit tests cover **6/6** sources and run in CI in **≤ 10 minutes**

---

## Acceptance Tests

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AT-001 | Full load, happy path | Generator output for seed 42 in all 6 formats; SQL Server/Postgres/Mongo loaded; gateway online | The orchestrating pipeline runs on an empty dev workspace | 73 Bronze tables exist with source row counts; the convergence check passes; Gold reconciliation passes |
| AT-002 | Convergence catches a drift | A single row's `FATURAMENTO` in one source has been altered (e.g. in Postgres) | The pipeline runs | The convergence check fails, names the source, table and key, and Gold is **not** updated |
| AT-003 | Leading zeros preserved | CSV and Postgres source codes such as `COD_FABRICA = "002"` | Silver adapters run | Canonical codes equal `"002"` in all 6 sources (never `2`) |
| AT-004 | Mongo denormalised `sales` | Mongo `sales` documents with embedded `cliente` / `produto` / `fabrica` / `organizacional` and `frete` | The Mongo adapter runs | It produces `tab_fato001` and `tab_fato002` identical to the other sources' canonical output |
| AT-005 | Postgres lower-case identifiers | Postgres tables with lower-cased column names | The Postgres adapter runs | Canonical column names match the reference schema exactly |
| AT-006 | Idempotent rerun | A completed full load | The pipeline runs again with no source changes | 0 fact rows inserted; Gold totals unchanged |
| AT-007 | Incremental load | Source facts extended with new `DATA_FATO` days after a completed load | The pipeline runs | Only rows after the watermark are ingested; Gold reconciles to the new source totals |
| AT-008 | Source unavailable | The gateway or one database container is offline | The pipeline runs | The run fails visibly at that source's ingestion; Silver/Gold are not published from partial data |
| AT-009 | Rebuild after trial expiry | A new empty workspace and the Git repo | `fabric-cicd` deploys, then the pipeline runs | All items exist and AT-001's assertions pass |
| AT-010 | Promotion to prod | A validated dev workspace | The deployment pipeline promotes dev → prod | Prod items point to prod lakehouse/warehouse/connections (deployment rules), and the prod report renders |
| AT-011 | RLS by region | Semantic model with a region role | A test user in role `Sudeste` queries revenue | Only `Sudeste` rows are visible; the total equals Gold's `Sudeste` sum |
| AT-012 | Org hierarchy flattened | `dim_organizacional` nested set (`Esquerda` / `Direita` / `Nivel`) | Gold dimension build runs | Each leaf org has its full ancestor path as columns, and fact revenue rolls up to the root equal to the grand total |

---

## Out of Scope

- Streaming / Real-Time Intelligence / Eventstream / Kafka (the generator's stream code was removed)
- Dataflow Gen2
- A test environment: dev + prod only
- Azure DevOps (GitHub Actions only)
- SCD Type 2 dimensions
- More than one Power BI report; KPI marts
- Refitting the generator model or fidelity checks against the original 2013–2015 data
- Committing generated data to Git
- Terraform or other IaC for Azure resources. The capacity is a manually provisioned trial, and the gateway is installed by hand once
- Performance tuning beyond meeting the 60-minute end-to-end target

---

## Constraints

| Type | Constraint | Impact |
|------|------------|--------|
| Resource | Fabric **trial** capacity (time-limited) | Everything must be code in Git; rebuild-from-Git is a MUST (G6) |
| Technical | Database sources run in local Docker Compose on the author's Windows machine | An **on-premises data gateway** must run on that machine; DB ingestion only works while it's online |
| Technical | MongoDB via gateway + Copy activity is unverified | Design must confirm the connector; fallback is ingesting the Extended-JSON export (`sources/data/mongodb/`) as the Mongo source |
| Technical | Source measures are `FLOAT`/`DOUBLE` in SQL Server/Postgres, doubles in Mongo/JSON/Parquet, text in CSV | Canonical Silver measures are `DECIMAL(18,2)` so that values already rounded to 2 places compare exactly |
| Technical | Codes are zero-padded strings; Postgres lower-cases identifiers; Mongo uses lower-case nested fields and `$date` | Each source needs its own adapter; codes must never be inferred as numbers |
| Technical | Generated data is not in Git | Every run needs `uv run generator generate` (+ `load`) first; seed 42 makes it byte-identical |
| Technical | Python 3.11+, `uv`, `fabric-cicd`, service principal with workspace access; tenant setting allowing SPN API access | SPN tenant settings must be enabled on the trial tenant |
| Timeline | No fixed deadline; bounded by the trial period | Build order should deliver an end-to-end vertical slice early |

---

## Technical Context

| Aspect | Value | Notes |
|--------|-------|-------|
| **Deployment Location** | `sources/` (generator, done); `workspace/` (Git-synced Fabric item definitions); `notebooks/` (Silver adapters, convergence), `warehouse/` (Gold T-SQL), `semantic-model/`, `tests/` (local PySpark + reconciliation), `.github/workflows/` | Matches the planned layout in `README.md`; Fabric items stay in the Git folder format that `fabric-cicd` expects |
| **KB Domains** | `microsoft-fabric` (medallion-fabric, lakehouse, spark-notebooks, copy-activity, incremental-load, lakehouse-vs-warehouse, hybrid-architecture, warehouse-basics, t-sql-patterns, star-schema, direct-lake, rls-security, purview-integration, git-integration, deployment-pipelines, deployment-rules, environment-promotion, delta-lake-optimization), `medallion`, `lakehouse`, `data-modeling`, `data-quality`, `spark`, `sql-patterns`, `testing` | Primary sources of patterns for Design |
| **IaC Impact** | New resources (Fabric items via `fabric-cicd`); no Azure IaC | Workspaces, lakehouses, warehouse, pipeline, notebooks, semantic model, report, connections, gateway (manual, one-off), service principal (manual, one-off) |

---

## Data Contract

### Source Inventory

| Source | Type | Volume | Freshness | Owner |
|--------|------|--------|-----------|-------|
| CSV | Files → OneLake `lh_bronze/Files/csv/` | 250 MB; ~3.4M fact rows | On demand (regenerated) | `sources/` generator |
| Parquet | Files (hive `year=`) → OneLake | 30 MB; ~3.4M fact rows | On demand | `sources/` generator |
| JSON Lines | Files → OneLake | 610 MB; ~3.4M fact rows | On demand | `sources/` generator |
| SQL Server 2025 | Database `labsources` via gateway | ~3.4M fact rows | After `generator load` | `sources/` docker compose |
| PostgreSQL 17 | Database `labsources` via gateway | ~3.4M fact rows | After `generator load` | `sources/` docker compose |
| MongoDB 8 | Database `labsources` via gateway (fallback: Extended-JSON files) | 12 flat collections + `sales` (1,050,428 docs) | After `generator load` | `sources/` docker compose |

### Schema Contract (canonical Silver, all sources)

Facts:

| Table | Column | Type | Constraints | PII? |
|-------|--------|------|-------------|------|
| all facts | `DATA_FATO` | TIMESTAMP | NOT NULL | No |
| all facts | `COD_DIA` | STRING (`YYYYMMDD`) | NOT NULL, FK → `dim_tempo.Cod_Dia` | No |
| 001, 002, 004 | `COD_CLIENTE` | STRING (zero-padded) | NOT NULL, FK → `dim_cliente` | No |
| 001, 002, 004, 005 | `COD_PRODUTO` | STRING | NOT NULL, FK → `dim_produto` | No |
| 001, 002, 003, 005 | `COD_FABRICA` | STRING (e.g. `"002"`) | NOT NULL, FK → `dim_fabrica` | No |
| 001, 004 | `COD_ORGANIZACIONAL` | STRING | NOT NULL, FK → `dim_organizacional.Cod_Filho` | No |
| 001 | `FATURAMENTO`, `IMPOSTO`, `CUSTO_VARIAVEL`, `UNIDADE_VENDIDA`, `QUANTIDADE_VENDIDA` | DECIMAL(18,2) | NOT NULL, ≥ 0 | No |
| 002 / 003 / 004 / 005 | `FRETE` / `CUSTO_FIXO` / `META_FATURAMENTO` / `META_CUSTO` | DECIMAL(18,2) | NOT NULL, ≥ 0 | No |

Primary keys: 001 (CLIENTE, PRODUTO, ORGANIZACIONAL, FABRICA, DIA); 002 (CLIENTE, PRODUTO, FABRICA, DIA); 003 (FABRICA, DIA); 004 (CLIENTE, PRODUTO, ORGANIZACIONAL, DIA); 005 (PRODUTO, FABRICA, DIA).

Dimensions keep the reference names and types from `sources/src/generator/dims.py` and `timedim.py`: all `Cod_*`/`Desc_*` are STRING; `Esquerda`/`Direita`/`Nivel` and `dim_tempo`'s `Cod_Semana`/`Cod_Mes`/`Cod_Trimestre`/`Cod_Semestre` are INT; `dim_tempo.Data` is DATE. `dim_organizacional.Cod_Pai` is nullable (root). Client names and cities are fictional business entities, so there is no PII.

Derived-key invariants (from `schema.py`): 002 has one row per 001 row; 003, 004 and 005 key sets equal the distinct projections of 001.

### Freshness SLAs

| Layer | Target | Measurement |
|-------|--------|-------------|
| Bronze | Within the pipeline run that follows `generator generate`/`load` | `_ingested_at` ≥ run start |
| Silver / Gold | Same pipeline run as Bronze, ≤ 60 min end to end | Pipeline run duration |

### Completeness Metrics

- 100% of source rows present in Bronze for every source (row count equality)
- 6/6 sources converge with 0 differing rows per table
- 0 null PKs, 0 duplicate PKs, 0 orphan FKs in Silver and Gold
- Gold measure totals equal source totals to 0.00 per year

### Lineage Requirements

- Every Bronze row carries `_source`, `_ingested_at` and `_batch_id`
- Table-level lineage source → Bronze → Silver → Gold → semantic model visible in Fabric lineage view; Purview lineage is COULD (G12)

---

## Assumptions

| ID | Assumption | If Wrong, Impact | Validated? |
|----|------------|------------------|------------|
| A-001 | The Fabric pipeline Copy activity can read on-prem MongoDB through the data gateway | Mongo ingests through the Extended-JSON export files instead; the "live DB" story for Mongo is lost | [x] Microsoft Learn: Copy activity source supports on-premises gateway |
| A-002 | The trial tenant allows enabling service principal API access and Git integration | `fabric-cicd` must run as a user identity; CI rebuild becomes semi-manual | [ ] |
| A-003 | The on-prem gateway on the Windows host can reach the Compose containers at `127.0.0.1:1440/5432/27017` | Ports or networking must change (e.g. bind to the host IP) | [ ] |
| A-004 | Casting each source's measures to DECIMAL(18,2) gives bit-identical values across sources (generator rounds to 2 dp; CSV writes shortest round-trip floats) | Convergence needs a tolerance, which weakens the "identical" claim | [ ] |
| A-005 | The reference total 2,664,425,394.54 (upstream README, seed 42) is reproduced by the vendored generator | Reconciliation target must be recomputed from the generated Parquet (it's derived from it anyway) | [ ] |
| A-006 | The trial capacity runs ~20M Bronze rows plus Silver/Gold within 60 minutes | Relax the SLA or reduce Bronze to a subset of years for runs | [ ] |
| A-007 | Direct Lake works on a Warehouse populated by T-SQL procedures without falling back to DirectQuery for these volumes | Accept fallback or move Gold tables to a Lakehouse (Approach A) | [ ] |
| A-008 | Fabric deployment pipelines and `fabric-cicd` can coexist (`fabric-cicd` targets dev only; the deployment pipeline owns prod) | Pick one promotion mechanism; drop G8 or narrow `fabric-cicd` | [ ] |

---

## Clarity Score Breakdown

| Element | Score (0-3) | Notes |
|---------|-------------|-------|
| Problem | 3 | One specific problem: six source shapes, no proof of convergence, cert + portfolio need |
| Users | 2 | Two personas with pain points; the "portfolio reviewer" persona is necessarily generic |
| Goals | 3 | 13 goals with MoSCoW priority, traceable to brainstorm decisions |
| Success | 3 | Every criterion has a number (row counts, 0.00 deltas, 60 min, 10 min, 1 run) |
| Scope | 3 | Explicit out-of-scope list from the YAGNI pass; prerequisites separated |
| **Total** | **14/15** | |

---

## Open Questions

None block Design. These must be resolved **in Design** (tracked as assumptions above):

1. A-001: MongoDB connector support through the gateway. Decide between live DB and file fallback.
2. Reference source for the convergence `EXCEPT` comparisons (proposal: Parquet, the generator's typed output).
3. Fabric Git folder layout vs. the repo's `notebooks/` / `warehouse/` split. Where `fabric-cicd` reads item definitions from.

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-10-02 | define-agent | Initial version from BRAINSTORM_FABRIC_MEDALLION_LAKEHOUSE.md |
| 1.1 | 2026-10-02 | design-agent | Status → Complete (Designed); Bronze count 72 → 73 (Mongo `sales` lands as its own table); A-001 validated |

---

## Next Step

**Ready for:** `/build .claude/sdd/features/DESIGN_FABRIC_MEDALLION_LAKEHOUSE.md`
