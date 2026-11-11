## Context

Source state: post-revision-11 working tree (issue #1 adopted it as the root). The campaign
framework (`brief/handoff/workflow-contract`, `make_status`, staged reports) produced and
validated revisions 1–11; the export to Git never included the local artifact directories
(`artifacts/checks/...`, `artifacts/rev10/...`), so the monolithic `verify_e2e.py` and
`rev10_verify.py` are not reproducible from a clean Git copy today.

Purpose of the repository going forward: run cases, measure metrics at defined points, compute
L* at the end (`report_cli offline/live`, runner → evaluator → state_store → metrics). Campaign
bookkeeping is not needed.

## Goals / Non-Goals

- Goals: a repository that reproduces its verification from a clean copy; removal of one-off
  revision tooling and campaign bookkeeping; preservation of scientifically significant
  artifacts and the standing test/mutation suite; archive of the pre-cleanup state.
- Non-Goals: removing `darp_range` components (inventory of modules shows live imports for
  each; strategy A); rewriting the measurement core; renaming standing tests (cosmetic rename
  pass deferred).

## Decisions

- Decision: replace the monolith instead of trimming it. `verify_e2e.py` chains 9 closed
  revisions' checks with hardcoded script calls and artifact dependencies; a slim
  `scripts/verify.py` (pattern of `rev11_verify.py`) keeps only the standing chain. Rationale:
  the old verify already fails from a clean copy, and every removed step validated a closed
  revision.
- Decision: delete `rev10_verify.py` as well. Its unique checks (historical tamper controls,
  legacy-column regression, preregistration byte checks) target closed-revision artifacts; the
  standing v2r/broker/isolation contracts are covered by `test_rev10_*`, `test_rev11_*`,
  `test_review_routing_v2r`, `test_server_invariants_v2` (which run from a clean copy).
- Decision: keep the entire mutation suite (`mutate_*.py`). They are RED/GREEN tests against
  the current code, not one-off scripts. The slim verify runs the mutation set previously
  wired into verify (confirm ×2, rev6, rev7, rev8, rev10) in one step; per-component mutators
  stay as development tools. `mutate_rev10.py` loses the two mutants patching the deleted
  `historical.py`.
- Decision: test deletion is limited to tests whose subject is a closed revision or a deleted
  script. The issue's figure «39 ревизионных тестов» includes tests of standing contracts
  (metrics formulas, confirm contract, legal user, live modes, queue acceptance); deleting
  those would silently drop coverage of the measurement core. Classification per file is in
  `inventory.md`.
- Decision: `decomposition.json` + `workflow-contract.json` + `validate_decomposition.py` +
  `test_decomposition.py` are deleted (campaign/factory artifact, agreed with the owner). The
  code-to-plan correspondence is not maintained going forward; component demos remain in the
  slim verify as smoke probes.
- Decision: artifact policy — keep preregistrations (protocol of the campaign), attack-cards
  (scenario passports referenced by tests/reports), the final report and its history. Delete
  per-revision plan/budget/results working copies (originals preserved in `revisions/` and
  under the archive tag), campaign status/improvement bookkeeping, `simplifications.json`.
- Decision: archive via annotated tag `archive/rev11` on `develop` before the deletion merge;
  the same snapshot already exists physically in `revisions/darp-revision-11/prototype/`.
- Decision: `brief.md` (the original ТЗ) moves to `docs/brief.md`; `handoff.json`,
  `progress.md`, `workflow-contract.json` are deleted.

## Risks / Trade-offs

- A standing behavior might turn out to be checked only by a deleted script; mitigated by the
  dependency scan recorded in `inventory.md` and by running the full unittest suite + slim
  verify before merge.
- The slim verify no longer reproduces revision 1–9 historical tables — accepted: those results
  are frozen in `revisions/` reports, not re-derivable anyway (missing local artifacts).
- `test_legal_facts_mode.py` accumulates probe strings from confirm test modules and optional
  artifact files; all its sources stay, so no behavior change.
- Docstrings in `darp_range/` referencing deleted scripts (`metrics/report.py` →
  `rev4_stepshield_diag`, `report_cli/acceptance.py` → `verify_e2e`) are updated; frozen
  defense-code files stay untouched (docstring mentions there are historical references and
  remain as-is).

## Migration Plan

1. Inventory + dependency scan (this change), agreement with the issue author.
2. Tag `archive/rev11` on `develop`.
3. Deletion + `scripts/verify.py` + test adaptations in this branch.
4. Acceptance: full unittest, `scripts/verify.py` from a clean copy, `report_cli offline`
   table L* from a clean copy; PR into `develop`.
