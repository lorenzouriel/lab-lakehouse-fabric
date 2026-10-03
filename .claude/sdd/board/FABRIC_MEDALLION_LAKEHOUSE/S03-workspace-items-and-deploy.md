---
id: S03
feature: FABRIC_MEDALLION_LAKEHOUSE
title: "Add lakehouse items, fabric-cicd deploy, and the one-off setup guide"
status: backlog
depends_on: [S01]
agent: fabric-cicd-specialist
files: [workspace/lh_bronze.Lakehouse/.platform, workspace/lh_silver.Lakehouse/.platform, workspace/parameter.yml, scripts/deploy.py, .github/workflows/deploy-dev.yml, docs/setup.md]
updated: 2026-10-02
---

## Story 3: Add lakehouse items, fabric-cicd deploy, and the one-off setup guide (workspace/*.Lakehouse, parameter.yml, scripts/deploy.py, deploy-dev.yml, docs/setup.md)

**Description:** Makes the repo deployable. It adds the two lakehouse items, `scripts/deploy.py` (fabric-cicd), the GitHub Actions deploy-on-merge workflow, and the manual setup guide that validates assumptions A-002 (SPN API access on the trial tenant) and A-003 (gateway reaches the Docker ports). Per DESIGN Decision 4, `fruitjuice-dev` is deployed only by `fabric-cicd` and is not Git-connected. This is a prerequisite for S04: nothing runs in Fabric before this merges.

**Actual Plan**

1. `workspace/lh_bronze.Lakehouse/{.platform, lakehouse.metadata.json}` and `workspace/lh_silver.Lakehouse/{…}` (new). `.platform` uses `type: Lakehouse` with a fixed `logicalId` GUID per item. The metadata file is `{}` (no schemas), so tables live under `dbo`.
2. `workspace/parameter.yml` (new). Start with an empty `find_replace: []` plus a comment listing the placeholders later stories add: connection GUIDs (S04), lakehouse IDs (S04/S05), Warehouse SQL endpoint (S07). The format follows fabric-cicd's documented `find_replace` / `replace_value: {DEV: ...}`.
3. `scripts/deploy.py` (new), exactly as DESIGN Pattern 7:
   - `ITEM_TYPES = ["Lakehouse", "Warehouse", "Notebook", "DataPipeline", "SemanticModel", "Report"]`.
   - Env vars: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `FABRIC_WORKSPACE_ID`, `FABRIC_ENV` (default `DEV`).
   - `publish_all_items` then `unpublish_all_orphan_items`.
   - `--interactive` flag: use `AzureCliCredential` instead of the SPN (fallback if A-002 fails; default stays SPN).
   - Exit code 1 with a clear message when an env var is missing.
4. `.github/workflows/deploy-dev.yml` (new):
   - Triggers: `push` to `main` with paths `workspace/**`, `scripts/deploy.py`; plus `workflow_dispatch` with a `workspace_id` input (the rebuild target, AT-009).
   - Steps: `astral-sh/setup-uv`, `uv sync`, `uv run python scripts/deploy.py`.
   - Secrets: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `FABRIC_DEV_WORKSPACE_ID`.
5. `docs/setup.md` (new), numbered checklist:
   1. Trial capacity.
   2. Tenant settings: "Service principals can use Fabric APIs" and Git integration.
   3. Entra app + secret, given Contributor on `fruitjuice-dev` only.
   4. Workspaces `fruitjuice-dev` and `fruitjuice-prod`, plus feature branch-out workspaces.
   5. Install the on-prem data gateway on the Docker host.
   6. Create 3 connections (SQL Server `127.0.0.1,1440` db `labsources`; PostgreSQL `127.0.0.1:5432` db `labsources`; MongoDB `127.0.0.1:27017` db `labsources`, Basic, `authSource=admin`), with credentials from `sources/.env.example:11-43`, each with "Test connection". This is the A-003 check.
   7. Record the 3 connection GUIDs for S04.

   Each step has an "expected result" line, and a "Results" table records A-002/A-003 outcomes.

**Architecture**

```text
git main ──push──► deploy-dev.yml ──► scripts/deploy.py (SPN)
                                         │ fabric-cicd publish_all_items
                                         ▼
                               fruitjuice-dev: lh_bronze, lh_silver
docs/setup.md: tenant + SPN + gateway + 3 connections  [validates A-002, A-003]
```

**Acceptance Criteria**

- [ ] `uv run python scripts/deploy.py` against an empty workspace creates `lh_bronze` and `lh_silver`; a second run makes no changes and leaves no orphans
- [ ] `deploy-dev.yml` runs on merge to `main` and via manual dispatch with a different `workspace_id`
- [ ] No secret, GUID of a real tenant, or connection string is committed (grep for GUID patterns outside `.platform` logicalIds and placeholders)
- [ ] `deploy.py` exits 1 with a message naming the missing variable when any required env var is absent
- [ ] `docs/setup.md` records the A-002 and A-003 outcomes (pass/fail) in its Results table
- [ ] `fruitjuice-dev` is not connected to Git (the setup guide states it explicitly)
- [ ] `ci.yml` from S01 stays green (`ruff` covers `scripts/deploy.py`)
