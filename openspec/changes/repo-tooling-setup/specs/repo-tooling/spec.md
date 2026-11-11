## ADDED Requirements

### Requirement: Blocking lint gate
`ruff check` SHALL pass on the whole working tree with the repository configuration
(`pyproject.toml [tool.ruff]`), and CI SHALL fail a PR when it does not.

The lint configuration SHALL be minimal:
- `select = ["E4", "E7", "E9", "F"]` (pyflakes + error-level pycodestyle, no `E501`);
- `target-version = "py311"`;
- `revisions/` excluded from linting.

#### Scenario: Clean tree passes
- **WHEN** `ruff check` runs at the repository root
- **THEN** the exit code is 0

#### Scenario: Archive is not linted
- **WHEN** the configuration changes or a file under `revisions/` contains lint findings
- **THEN** `ruff check` does not report them and the exit code stays 0

### Requirement: Frozen defense code is not edited by tooling
Lint findings inside the frozen defense-code directories (`configs`, `guard_contract`,
`guard_plugins`, `guard_policy`, `simple_baseline`, `executor`, `tools` — the `DEFENSE_CODE`
hash tree of `configs.lock.json`) SHALL be suppressed by targeted `per-file-ignores` instead of
editing the files, until the defense code is deliberately changed and re-frozen.

#### Scenario: Revert preserves the freeze
- **WHEN** the tooling-only changes are applied to the working tree
- **THEN** the byte content of every defense-code file matches `configs.lock.json`
  and `load_frozen` accepts it

### Requirement: Commit gate
Commit messages SHALL follow Conventional Commits (English), enforced by a commitizen hook on
commit-msg. Hooks include ruff for lint/format on pre-commit.

#### Scenario: Bad commit message rejected
- **WHEN** a commit message does not match the Conventional Commits format
- **THEN** the commit-msg hook fails and the commit is not created

### Requirement: Openspec process for substantive changes
Any substantive change to the prototype or the repository SHALL be accompanied by a change
catalog `openspec/changes/<change-id>/` containing `.openspec.yaml`, `proposal.md`,
`design.md`, `tasks.md` and `specs/<capability>/spec.md`, following the revision 9–11 format.
A change without a specification SHALL NOT be accepted.

#### Scenario: Self-applied process
- **WHEN** the tooling setup itself is introduced
- **THEN** it is recorded as `openspec/changes/repo-tooling-setup/` in the same format

### Requirement: Fast PR-only CI
The repository SHALL have exactly one GitHub Actions workflow, triggered on pull requests
targeting `develop`/`main`, with two blocking jobs: `ruff check` and commitizen check of the
PR title (squash-merge turns the PR title into the commit message). Unit tests and the full
offline verify (~53 min) MUST NOT run in CI.

#### Scenario: Lint failure blocks PR
- **WHEN** a PR introduces a file with ruff findings
- **THEN** the CI workflow fails and blocks the merge

#### Scenario: Non-conventional PR title blocks merge
- **WHEN** a PR title does not match Conventional Commits
- **THEN** the `commit-msg` job fails and blocks the merge

### Requirement: Issue-branch-PR workflow
Every task SHALL exist as an issue on the DVARP Board (`CloudNative-urfu/projects/1`) with an
assignee from the team. Implementation SHALL happen only in a task branch
(`feat/<issue-number>-<slug>` etc.) branched from `develop`; merging SHALL happen only through
a pull request into `develop` (squash-merge, description containing `Closes #<issue-number>`).
Substantive changes (prototype code, experiments, measurements, process) SHALL additionally
carry an openspec change catalog; technical tasks (infrastructure, documentation, `chore`) may
go without a spec — but always through a task branch and PR. Direct pushes to `develop` in a
task context and any direct pushes to `main` are forbidden.

#### Scenario: Substantive change lands on the board
- **WHEN** an agent takes a `Ready` issue, implements it in a task branch, opens a PR into
  `develop` and the PR is squash-merged
- **THEN** the issue closes automatically via `Closes #N` and its board status becomes `Done`

#### Scenario: Direct push rejected
- **WHEN** an agent attempts to push directly to `develop` while working on an issue, or to
  `main` at any time
- **THEN** the push is not performed; the agent reports and uses a task branch + PR instead
