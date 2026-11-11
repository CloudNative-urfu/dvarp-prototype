## Why

The repository currently holds an archival snapshot chain (`revisions/darp-revision-{2..11}`) with
no code-quality tooling, no CI and no commit discipline. It now becomes the working repository of
the prototype; the source state is the revision 11 snapshot (`revisions/darp-revision-11/prototype/`,
moved to the root). Without a minimal tooling baseline every later change lands unreviewed by linters,
with unverified commit messages and no automated gate (issue #1).

## What Changes

- Revision 11 prototype snapshot moves to the repository root (via `git mv`, history preserved);
  `revisions/` stays as the archive. The prototype README moves to `docs/PROTOTYPE.md`.
- ruff is configured as the single linter: default minimal rule set (`E4`, `E7`, `E9`, `F`,
  no `E501`), `target-version = "py311"`, `revisions/` excluded from linting. Local legacy warnings
  are fixed; the frozen defense-code files (`configs.lock.json` hash tree) are not edited and get
  targeted `per-file-ignores` until re-freeze.
- The openspec process becomes mandatory at the root level: any substantive change must be
  accompanied by `openspec/changes/<change-id>/` (proposal, design, tasks, capability specs),
  following the revision 9–11 format. A change without a spec is rejected.
- pre-commit hooks: ruff (lint on commit) and commitizen (commit-msg, Conventional Commits,
  English).
- CI (GitHub Actions, PR-only): blocking `ruff check`. Unit tests and the full e2e verify
  (~53 min) stay local by design.
- README and AGENTS.md are rewritten for the working repository: commit rules, auto-commit
  permission, the openspec process, local verification commands. AGENTS.md follows the dvarp
  repository template: issue ↔ board rule, branch model (`develop` integration default,
  protected `main`), task-branch + PR workflow (openspec catalog required for substantive
  changes), board status semantics. CI gains a blocking commit-msg check of the PR title.

## Capabilities

### New Capabilities
- `repo-tooling`: lint gate, commit gate, openspec process, CI gate.

### Modified Capabilities
<!-- none: prototype behavior is unchanged; tooling only -->

## Impact

- New files: `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, root `openspec/` documentation
  references in README/AGENTS.md.
- `pyproject.toml` gains a `[tool.ruff]` section; ~30 legacy files get lint-cleanups (import removals,
  lambda→def, semicolon splits, `l` renames). Frozen defense-code files are untouched.
- Developer environment: ruff, pre-commit and commitizen installed locally (e.g. `uv tool install`).
- The offline verify path (`scripts/verify_e2e.py`) and CLI (`python3 -m darp_range.report_cli
  offline`) are not affected.
