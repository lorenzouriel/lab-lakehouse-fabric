---
id: S07
feature: FABRIC_MEDALLION_LAKEHOUSE
title: "Prove incremental and idempotent runs with an end-to-end script"
status: backlog
depends_on: [S06]
agent: shell-script-specialist
files: [scripts/generate_partial.py, scripts/e2e.sh, tests/e2e/test_incremental.py]
updated: 2026-10-02
---

## Story 7: Prove incremental and idempotent runs with an end-to-end script (generate_partial.py, e2e.sh, test_incremental.py)

**Description:** Adds the one-command end-to-end run (generate → load → upload → pipeline → e2e tests) and the incremental scenario: load a dataset truncated at 2026-06-30, then the full dataset, and prove only new rows flow through and Gold reconciles each time (AT-001, AT-006, AT-007). It relies on the generator property that extending `period.end` never changes earlier data (`sources/tests/test_generator.py:54-63`). Depends on S06 because watermarks only advance once the full chain exists.

**Actual Plan**

1. `scripts/generate_partial.py` (new):
   - CLI `--end YYYY-MM-DD`.
   - Inserts `sources/src` on `sys.path`, then `generate(load_config(**{"period.end": end}))`, using `sources/src/generator/config.py:16` and `pipeline.py:16`.
   - Writes to the default `sources/data` (`config.py:10`), so `generator load` and `upload_files.py` pick it up unchanged.
   - Prints the row counts returned by `generate`.
2. `scripts/e2e.sh` (new, `set -euo pipefail`):
   - Default mode:
     1. `(cd sources && uv run generator generate && uv run generator load)`
     2. `uv run python scripts/upload_files.py`
     3. `uv run python scripts/run_pipeline.py --param full_reload=true`
     4. `uv run pytest -m e2e tests/e2e`
   - `--incremental` mode:
     1. `generate_partial.py --end 2026-06-30`, then load + upload + run (`full_reload=true`) + pytest;
     2. full generate + load + upload + run (`full_reload=false`) + pytest;
     3. run again with no changes and assert idempotency via `test_incremental.py`.
   - Requires `FABRIC_WORKSPACE_ID` and the SQL endpoint env vars; fails early with a message if missing.
3. `tests/e2e/test_incremental.py` (new, `pytest.mark.e2e`):
   - `test_last_batch_inserted_zero_rows`: the latest `ctl_load_log` batch has `rows = 0` for every fact × source.
   - `test_watermark_equals_max_source_date`: each `ctl_watermark` row equals the max `DATA_FATO` in local Parquet.
   - `test_silver_counts_unchanged_between_last_two_batches` (from `ctl_load_log` + table counts).
   - Reconciliation is re-asserted by reusing `tests/e2e/test_reconciliation.py` in the same session.

**Architecture**

```text
e2e.sh --incremental
  generate_partial(end=2026-06-30) ─► load ─► upload ─► run(full_reload) ─► pytest e2e   [reconciles]
  generate(full)                   ─► load ─► upload ─► run(incremental)  ─► pytest e2e   [only Jul–Dec 2026 rows]
  run(incremental, no changes)                                             ─► test_incremental  [0 rows, totals same]
```

**Acceptance Criteria**

- [ ] `scripts/e2e.sh` on an empty dev workspace (after `deploy.py`) exits 0 (AT-001)
- [ ] `scripts/e2e.sh --incremental` exits 0, and the second run's `ctl_load_log` fact rows equal only rows with `DATA_FATO > 2026-06-30` (AT-007)
- [ ] The third (no-change) run inserts 0 Bronze fact rows and Gold totals are unchanged (AT-006)
- [ ] Gold reconciles to 0.00 after each of the three runs
- [ ] `e2e.sh` stops at the first failing step with that step's exit code (`set -euo pipefail`)
- [ ] The end-to-end pipeline duration of the full run is recorded in BUILD_REPORT against the ≤ 60 min target (A-006)
- [ ] No changes to `sources/src/generator/*` (the partial generate uses the public `load_config`/`generate` API only)
