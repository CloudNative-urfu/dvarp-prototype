## 1. Tasks

- [x] repo-state Move revision 11 prototype snapshot to the repository root (`git mv`), archive README to `docs/PROTOTYPE.md`, merge prototype `.gitignore` into root
- [x] ruff-config `pyproject.toml`: `select = ["E4", "E7", "E9", "F"]`, `target-version = "py311"`, `revisions/` excluded, targeted `per-file-ignores` for frozen defense-code files
- [x] lint-cleanup Mechanical cleanups in non-frozen files (unused imports, lambda assignments, semicolon chains, ambiguous `l` names, import order) — ~35 findings, `ruff check` passes on the whole working tree
- [x] pre-commit `.pre-commit-config.yaml` with ruff and commitizen (commit-msg, Conventional Commits, English); hooks installed and green on all files
- [x] ci `.github/workflows/ci.yml` — PR-only blocking `ruff check`; tests and full verify stay local
- [x] docs README and AGENTS.md rewritten for the working repository: openspec process, commit rules, auto-commit permission, local verification commands
- [x] workflow-rules AGENTS.md adapted from the dvarp repository: issue ↔ board rule, branch model (`develop` integration default, protected `main`), task-branch + PR workflow (spec required for substantive changes), board status semantics, auto-commit permission in task branches; CI gains a blocking commit-msg check of the PR title

## Notes

Test details are in the spec scenarios below. The frozen defense-code files
(`darp_range/{configs,guard_contract,guard_plugins,guard_policy,simple_baseline,executor,tools}/`)
are NOT edited in this change; warnings there are suppressed via `per-file-ignores` until
re-freeze. Final acceptance: full `python3 scripts/verify_e2e.py` from a clean copy.

Open follow-ups: `pre-commit install` on the developer machine, first CI run on GitHub,
and the full offline verify are pending local execution.
