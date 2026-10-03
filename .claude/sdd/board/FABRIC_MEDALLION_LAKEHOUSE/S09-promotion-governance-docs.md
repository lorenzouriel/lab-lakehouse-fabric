---
id: S09
feature: FABRIC_MEDALLION_LAKEHOUSE
title: "Promote dev to prod, add governance, and document the lab"
status: backlog
depends_on: [S07, S08]
agent: fabric-cicd-specialist
files: [docs/setup.md, README.md]
updated: 2026-10-02
---

## Story 9: Promote dev to prod, add governance, and document the lab (docs/setup.md, README.md)

**Description:** Closes the feature:
- configures the 2-stage Fabric deployment pipeline with deployment rules and promotes `fruitjuice-dev` → `fruitjuice-prod` (G8, AT-010);
- verifies a full rebuild into an empty workspace (G6, AT-009);
- adds the COULD-level governance: OneLake security on `lh_bronze` and Purview lineage (G12);
- rewrites the README.

It is the last story and depends on S07 (proven pipeline) and S08 (all items exist).

**Actual Plan**

1. `docs/setup.md`, new section "Promotion to prod" (manual portal steps, since deployment pipelines aren't Git items):
   - Create deployment pipeline `dp_fruitjuice` with stages Development = `fruitjuice-dev` and Production = `fruitjuice-prod`.
   - Deploy all items.
   - Deployment rules on the Production stage:
     - `sm_fruitjuice` data source rule → the prod `wh_gold` SQL endpoint;
     - notebooks' default lakehouse → prod `lh_bronze`/`lh_silver`, if not auto-bound.
   - Verify that prod `pl_fruitjuice_e2e` references prod items, the prod report renders, and running prod `pl_fruitjuice_e2e` reconciles: run `pytest -m e2e` with the prod endpoints exported.
2. `docs/setup.md`, new section "Rebuild after trial expiry" (AT-009):
   - Create an empty workspace.
   - `gh workflow run deploy-dev.yml -f workspace_id=<new>`.
   - Repeat the gateway connections and update the `parameter.yml` connection GUIDs if the connections were recreated.
   - Run `scripts/e2e.sh`. The expected result is all e2e tests passing.
3. `docs/setup.md`, new section "Governance" (COULD, G12):
   - OneLake security: data access role `BronzeEngineers` on `lh_bronze`, with read on `Tables/` and `Files/` granted only to the `engineers` group. Verify that a non-member viewer can't read `brz_*` from the SQL endpoint.
   - Purview: enable the Fabric scan and capture lineage source → Bronze → Silver → Gold → model.
   - If the trial tenant can't enable Purview, mark G12-Purview as not done in the Results table. Don't silently drop it.
4. `README.md` (rewrite of the current scaffold):
   - Status and architecture diagram, copied from DESIGN Architecture Overview.
   - Repo layout: `sources/`, `workspace/`, `scripts/`, `tests/`, `docs/`, `.github/`.
   - Quickstart: `docs/setup.md` → `deploy.py` → `scripts/e2e.sh`.
   - The convergence + reconciliation guarantees, with their numbers.
   - Links to the SDD docs.
   - Removes the "Scaffold only" status and the `../lab-sources` sibling link (now `sources/`).

**Architecture**

```text
main ─fabric-cicd─► fruitjuice-dev ─deployment pipeline (rules: SQL endpoint, lakehouses)─► fruitjuice-prod
empty workspace ─deploy-dev.yml (workflow_dispatch)─► rebuilt ─e2e.sh─► reconciles     [AT-009]
lh_bronze ◄─ OneLake security role BronzeEngineers                                       [G12]
Purview scan ─► lineage source→Bronze→Silver→Gold→model                                  [G12]
```

**Acceptance Criteria**

- [ ] Prod deploy via `dp_fruitjuice` succeeds; prod `sm_fruitjuice` points to prod `wh_gold` (deployment rule), and the prod report renders (AT-010)
- [ ] A prod pipeline run followed by `pytest -m e2e` against prod endpoints passes (reconciliation to 0.00)
- [ ] Rebuild into a brand-new empty workspace with `deploy-dev.yml` + `scripts/e2e.sh` passes with no manual item creation; manual steps are only connections/gateway (AT-009)
- [ ] The SPN has no role on `fruitjuice-prod` (verified in workspace access list)
- [ ] OneLake security role restricts `lh_bronze`, or `docs/setup.md` records why not on trial (G12 is COULD)
- [ ] `README.md` no longer says "Scaffold only" and contains no link to `../lab-sources`
- [ ] Every DEFINE acceptance test AT-001…AT-012 is ticked in `docs/setup.md`'s Results table or explicitly marked with the reason it wasn't met
