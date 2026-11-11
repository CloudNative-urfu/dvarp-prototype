## Context

Source state: revision 11 prototype snapshot from `darp-prototype-runs`, now at the repository
root. The prototype is standard-library-only Python (>= 3.11), verified by `scripts/verify_e2e.py`
(~53 min offline). A frozen-configuration mechanism (`darp_range/configs/freeze.py`, NFR-2.10)
hashes the defense-code directories (`configs`, `guard_contract`, `guard_plugins`, `guard_policy`,
`simple_baseline`, `executor`, `tools`) into `configs/configs.lock.json`; any byte in those files
must match the frozen hashes or `load_frozen` rejects the run.

Constraints from issue #1: minimal configuration (one lint tool, one workflow file), nothing that
obstructs further prototype work, the full verify from a clean copy must keep working.

## Goals / Non-Goals

- Goals: a blocking lint gate, a commit-msg gate, a documented openspec process, a fast CI check
  for PRs.
- Non-Goals: running tests or the full verify in CI; refactoring legacy code (issue #2);
  re-freezing defense code; introducing any runtime dependency.

## Decisions

- Decision: lint only the working tree. ruff 0.16 without config enables a very broad rule set
  (1000+ findings on this codebase). The explicit `select = ["E4", "E7", "E9", "F"]` (the classic
  ruff default, minus `E501`) keeps the gate to real defects: syntax errors, redefinitions,
  unused code, undefined names. `E501` is excluded because the codebase deliberately has no line
  length policy (~8000 lines in the 89–100 range). `revisions/` (8 archived prototype copies) is
  excluded from linting entirely.
- Decision: do not touch frozen defense-code files. ruff found four trivial warnings there
  (`F401`/`F541`), but editing those files invalidates `configs.lock.json` hashes and breaks the
  frozen-state tests. Cosmetic lint cleanups do not justify a re-freeze; instead they are
  suppressed via `per-file-ignores` until defense code is deliberately changed and re-frozen
  (issue #2 territory).
- Decision: clean up the rest of the legacy lint findings (~35 findings in non-frozen files:
  unused imports, lambda assignments, semicolon chains, ambiguous `l` names, module-level
  imports after code). All mechanical, behavior-preserving; confirmed by the offline test suite.
- Decision: CI runs only `ruff check` on PRs. The unit test suite and the full verify stay local:
  the issue explicitly forbids the ~53-minute verify in CI, and some tests require sandboxing
  tooling (bubblewrap) that is not guaranteed on the runner.
- Decision: openspec process is transplanted to the repository root and made mandatory for all
  substantive changes. The three prototype change catalogs (`attacker-isolation-boundary`,
  `trusted-trace-and-v2r-table`, `returns-operator-payout-slice`) stay as format references.
  This change itself (`repo-tooling-setup`) is the first root-level change — the process applies
  to its own introduction.
- Decision: commit messages are Conventional Commits in English (matches the existing history:
  `chore(research): ...`); commitizen enforces the format on commit-msg. Repository policy already
  permits auto-commits by agents (no GPG signing).
- Decision: developer tooling (ruff, pre-commit, commitizen) is installed user-side (e.g.
  `uv tool install`); no `flake.nix`, no dev-dependencies in `pyproject.toml` — the prototype
  keeps an empty runtime dependency list.

## Risks / Trade-offs

- `per-file-ignores` on frozen files hides four real warnings; accepted until re-freeze.
- CI without tests gives a fast but weak signal; the meaningful gate remains the local verify.
- Agent auto-commit permission and commitizen coexist: hooks run in the committing environment
  and may be skipped with `--no-verify` by explicit decision (documented in AGENTS.md).

## Migration Plan

1. Move the revision 11 snapshot to the root (`git mv`), verify CLI runs.
2. Land ruff config + legacy cleanups; verify `ruff check` passes.
3. Land pre-commit + CI + documentation.
4. Full local verify from a clean copy as final acceptance.
