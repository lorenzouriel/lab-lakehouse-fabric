---
id: S06
feature: FABRIC_MEDALLION_LAKEHOUSE
title: "Build the Gold warehouse star schema with load and reconcile procedures"
status: backlog
depends_on: [S05]
agent: sql-server-specialist
files: [workspace/wh_gold.Warehouse/wh_gold.sqlproj, workspace/wh_gold.Warehouse/gold/Tables/, workspace/wh_gold.Warehouse/gold/StoredProcedures/, workspace/nb_watermark_commit.Notebook/notebook-content.py, workspace/pl_fruitjuice_e2e.DataPipeline/pipeline-content.json, tests/e2e/test_reconciliation.py]
updated: 2026-10-02
---

## Story 6: Build the Gold warehouse star schema with load and reconcile procedures (wh_gold.Warehouse, nb_watermark_commit, pipeline, test_reconciliation.py)

**Description:** Adds the `wh_gold` Warehouse with schema `gold`: 5 dimensions, 5 facts, procedures that load from published Silver by three-part name, a reconcile procedure that fails the run on any mismatch, and the final watermark commit (DESIGN Decisions 3 and 7, Patterns 5–6). This completes the end-to-end pipeline, and reconciliation is proven against the local generator output. Prerequisite for S07 (incremental) and S08 (semantic model).

**Actual Plan**

1. `workspace/wh_gold.Warehouse/` (new SQL database project, the format Fabric Git uses for Warehouses):
   - `wh_gold.sqlproj`, `.platform`.
   - `gold/Tables/` with:
     - `dim_tempo` (`date_key DATE` PK-like + calendar columns from `sources/src/generator/timedim.py:13-21`);
     - `dim_cliente` (cod, desc, cidade, estado, regiao, segmento);
     - `dim_produto` (folds `dim_marca` + `dim_categoria`: cod, desc, tamanho, sabor, marca, categoria);
     - `dim_fabrica`;
     - `dim_organizacional` (cod_org, desc_org, nivel, nivel1..nivelN);
     - `fact_vendas` (001), `fact_frete` (002), `fact_custo_fixo` (003), `fact_meta_faturamento` (004), `fact_meta_custo` (005).
   - Measures are `DECIMAL(18,2)`, codes `VARCHAR`, and every fact has `data DATE` plus its code FKs.
   - Fabric Warehouse doesn't enforce PKs, so they're declared `NOT ENFORCED` where supported, for documentation only.
2. `gold/StoredProcedures/`:
   - `usp_load_dimensions`: truncate + insert for each dimension from `lh_silver.dbo.<dim>`. The org flattening uses the nested-set self-join from DESIGN Pattern 6. Before writing, check the max `Nivel` in `sources/src/reference/dim_organizacional.csv` and size `nivelN` to it.
   - `usp_load_facts @from_date DATE`: delete `>= @from_date` + insert, for each of the 5 facts (DESIGN Pattern 5).
   - `usp_load_all @from_date DATE`: calls both in a transaction.
   - `usp_reconcile`:
     - for each fact and each measure (9 measures per `sources/src/generator/schema.py:49-53`), year-sum `EXCEPT` between Silver and Gold in both directions;
     - row-count equality per fact;
     - org roll-up check: the sum of `fact_vendas.faturamento` joined to `dim_organizacional` at the root level equals the fact grand total (AT-012);
     - `THROW 50001, '<fact>.<measure> does not reconcile', 1` on the first mismatch.
3. **Risk R-1** (DESIGN Decision 3; flag in BUILD_REPORT):
   - First try procedures inside the SQL project, with a database reference to `lh_silver`.
   - If `fabric-cicd` publish fails on unresolved three-part names, use the fallback instead: move the procedure bodies to `warehouse/gold/*.sql` and run them through pipeline `Script` activities.
   - Record which path was taken.
4. `workspace/nb_watermark_commit.Notebook/notebook-content.py` + `.platform` (new; default lakehouse `lh_bronze`):
   - Parameter `batch_id`.
   - MERGE `ctl_watermark` with `max(DATA_FATO)` per source × fact from `brz_<src>_<fact>` (Mongo `sales`: `data.$date`).
   - This is the only writer of watermarks.
5. `workspace/pl_fruitjuice_e2e.DataPipeline/pipeline-content.json`: append, each `On success`:
   - `SP_usp_load_all`: Stored procedure activity on `wh_gold`, `@from_date = @activity('nb_silver_converge').output.result.exitValue` parsed `from_date`.
   - `SP_usp_reconcile`.
   - `nb_watermark_commit`.
6. `workspace/parameter.yml`: add the `wh_gold` id for the pipeline (`$items.Warehouse.wh_gold.$id`).
7. `tests/e2e/test_reconciliation.py` (new, `pytest.mark.e2e`; connects to the `wh_gold` SQL endpoint, env `FABRIC_GOLD_SQL_ENDPOINT`):
   - `test_measure_totals_per_year[fact-measure]`: 9 measures × years 2013–2026; Gold `SUM` == pyarrow sum over `sources/data/parquet/fruit_juice/<table>` rounded to 2 dp, with delta == 0.00.
   - `test_grand_total_faturamento`: == `2664425394.54` (A-005 sanity constant).
   - `test_row_counts[fact]`.

**Architecture**

```text
lh_silver.dbo.<t> ──3-part name──► gold.usp_load_all @from_date
                                     ├─ usp_load_dimensions (truncate/insert, org path flattened)
                                     └─ usp_load_facts (delete >= from_date, insert)
                                   ► gold.usp_reconcile  [THROW 50001 on any mismatch → stop]
                                   ► nb_watermark_commit  [only writer of ctl_watermark]
tests/e2e/test_reconciliation.py: Gold sums == local Parquet sums (0.00)
```

**Acceptance Criteria**

- [ ] After a full run on empty dev, `tests/e2e/test_reconciliation.py` passes: 9 measures × 14 years with delta 0.00, grand total `FATURAMENTO` = 2,664,425,394.54, fact row counts equal
- [ ] `usp_reconcile` throws 50001 when one Gold fact row is deleted manually (verified once, then data restored by rerunning `usp_load_all '1900-01-01'`)
- [ ] `dim_organizacional` has one row per `Cod_Filho` and the root-level roll-up equals the fact grand total (AT-012)
- [ ] `ctl_watermark` advances only in `nb_watermark_commit`, and only after `SP_usp_reconcile` succeeds (pipeline dependency chain verified in JSON)
- [ ] `usp_load_facts` called twice with the same `@from_date` leaves identical row counts (idempotent)
- [ ] BUILD_REPORT records whether R-1's primary path (procs in the SQL project) or the fallback (Script activities) was used
- [ ] No hardcoded workspace or item IDs in `.sql` files; cross-item references use the item name `lh_silver` only
