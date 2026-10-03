---
id: S08
feature: FABRIC_MEDALLION_LAKEHOUSE
title: "Add the Direct Lake semantic model with RLS and the report"
status: backlog
depends_on: [S06]
agent: fabric-architect
files: [workspace/sm_fruitjuice.SemanticModel/definition.pbism, workspace/sm_fruitjuice.SemanticModel/definition/, workspace/rpt_fruitjuice.Report/definition.pbir, workspace/rpt_fruitjuice.Report/definition/, workspace/parameter.yml]
updated: 2026-10-02
---

## Story 8: Add the Direct Lake semantic model with RLS and the report (sm_fruitjuice.SemanticModel, rpt_fruitjuice.Report, parameter.yml)

**Description:** Adds the serving layer:
- a Direct Lake semantic model on `wh_gold`, with star relationships and core measures;
- a static `Region` RLS role (DESIGN Decision 6);
- one PBIR report covering revenue, margin, freight and actual vs target (G5, G9).

It's independent of S07 and can run in parallel with it once S06 is done. Prerequisite for S09, since promotion needs every item to exist.

**Actual Plan**

1. `workspace/sm_fruitjuice.SemanticModel/` (new, TMDL):
   - `definition.pbism`, `.platform`.
   - `definition/model.tmdl`, `database.tmdl`.
   - `definition/expressions.tmdl`: the Direct Lake source, `Sql.Database("<wh_gold-sqlendpoint>", "<wh_gold-id>")` placeholders.
   - `definition/tables/*.tmdl`: one per Gold table, `mode: directLake`, partition `entityName` = the Gold table and `schemaName: gold`.
   - `definition/relationships.tmdl`: each fact → `dim_tempo[date_key]`, `dim_cliente`, `dim_produto`, `dim_fabrica`, `dim_organizacional` where the fact has the key. All single-direction, many-to-one.
   - Measures in `fact_vendas.tmdl`:
     - `Receita = SUM(fact_vendas[faturamento])`, `Impostos`, `Custo Variável`;
     - `Frete = SUM(fact_frete[frete])`, `Custo Fixo`;
     - `Margem = [Receita] - [Impostos] - [Custo Variável] - [Frete]`, `Margem % = DIVIDE([Margem], [Receita])`;
     - `Meta Receita`, `Atingimento Meta % = DIVIDE([Receita], [Meta Receita])`;
     - `Meta Custo`, `Frete % = DIVIDE([Frete], [Receita])`.
     - Fixed cost is shown separately rather than allocated, since cost allocation isn't in scope.
   - `definition/roles/Region.tmdl`: `modelPermission: read`, table permission on `dim_cliente` with filter `[regiao] = "Sudeste"`. One static role, used for the AT-011 demo.
2. `workspace/rpt_fruitjuice.Report/` (new, PBIR):
   - `definition.pbir` bound by path `../sm_fruitjuice.SemanticModel`.
   - One page:
     - KPI cards for Receita, Margem %, Frete % and Atingimento Meta %;
     - a line chart of Receita and Meta Receita by month;
     - a bar chart of Margem by `dim_produto[categoria]`;
     - a matrix of Receita by `dim_cliente[regiao]` × year;
     - slicers for year and region.
3. `workspace/parameter.yml`: `find_replace` the semantic model's SQL endpoint and warehouse id placeholders with `$items.Warehouse.wh_gold.$sqlendpoint` and `$items.Warehouse.wh_gold.$id`, `item_type: SemanticModel` (DESIGN Pattern 7).
4. Manual verification steps, appended to `docs/setup.md`:
   - **Direct Lake check:** "Direct Lake behavior: Direct Lake only"; then confirm no fallback by refreshing the report with "Performance analyzer" (no DirectQuery events).
   - **RLS check (AT-011):** "View as → Region". Receita equals `SELECT SUM(f.faturamento) FROM gold.fact_vendas f JOIN gold.dim_cliente c ON … WHERE c.regiao = 'Sudeste'`.

**Architecture**

```text
wh_gold.gold.* (Delta, V-Order) ──Direct Lake──► sm_fruitjuice
                                                   ├─ relationships: facts ─► dims (many-to-one)
                                                   ├─ measures: Receita, Margem %, Frete %, Atingimento Meta %
                                                   └─ role Region: dim_cliente[regiao] = "Sudeste"
                                                         ▼
                                                 rpt_fruitjuice (1 page, PBIR)
```

**Acceptance Criteria**

- [ ] `deploy.py` publishes `sm_fruitjuice` and `rpt_fruitjuice` to dev with no manual binding; the report renders all visuals
- [ ] Every table in the model is `directLake` mode; with the Direct Lake behavior set to "Direct Lake only", the report renders without error (no DirectQuery fallback)
- [ ] `Receita` (unfiltered) equals Gold `SUM(faturamento)` = 2,664,425,394.54
- [ ] "View as Region" shows only `Sudeste`, and its Receita equals the per-region Gold SQL sum (AT-011)
- [ ] No real workspace, item or endpoint IDs in the TMDL/PBIR files (placeholders + `parameter.yml` only)
- [ ] No Warehouse-level RLS or security policy is created (Decision 6)
