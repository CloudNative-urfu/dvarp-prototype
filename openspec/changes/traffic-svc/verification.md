# Verification — 2026-11-19

Base: develop e3a3ef5; branch: Issue-#36. Python 3.12.16, Windows.
Prepared for the issue #36 pull request into develop.

| Check | Result |
| --- | --- |
| `ruff check .` (0.17.5) | passed |
| `git diff --check` | passed |
| `python scripts/dev_smoke.py` | 281 tests, 11 modules, OK, 116.5 s (budget 300 s) |
| `python -m unittest tests.test_runner tests.test_state_store_deploy tests.test_revision_legal_runner -v` | 47 tests, OK, 40.4 s |
| `python -m unittest tests.test_report_cli.ContourTest.test_import_audit_real_package_is_stdlib_only -v` | passed; no third-party runtime imports |
| `python -m unittest tests.test_traffic_svc.TrafficTest.test_runner_reuses_snapshot_and_cleans_up_multiple_blocks -v` | passed after final cleanup adjustment |
| `cz check --message 'feat(research): implement snapshot-bound traffic service'` | passed |
| CLI, two configs/two blocks, command below | exit 0; 4/4 successful legal episodes per config; 0 svc errors |

```sh
python -m darp_range.runner svc --entries ctl-02,ctl-07 --n-legal 2 --blocks 2 \
  --configs g0,g_proc --out runs/issue-36-cli
```

Final logs: `runs/issue-36-validation/smoke-tests.log` and
`runs/issue-36-validation/regression-tests.log` (local ignored artifacts).
CLI artifacts: `runs/issue-36-cli/`; transient DB directories removed.

Traffic tests cover identical schedules, invite budgets, mismatched snapshot id/hash,
read-only binding to real rows, unchanged episode DB after bind, response/summary parity,
exact retry without double advancement, flow completion, corrupt manifest references,
service death, both services in the parity loop (outcomes/trajectories/final hashes/guard
decisions), shared physical CSV snapshot and process cleanup across blocks/configurations.
Existing defender-svc parity tests are also part of dev_smoke.

Environment failures found and fixed before the final green runs:

- Baseline defender parity used SQLite connections across distinct HTTP threads.
  A dedicated worker now owns all defender operations; defense directories unchanged.
- Two existing snapshot tests assumed POSIX file semantics or leaked a connection on an
  expected exception. They now close connections and simulate a crashed WAL writer.
- Temporary read-only snapshots survived runner cleanup on Windows. Cleanup now retries
  removal after making only run-owned temporary files writable.

Full `unittest discover` / `scripts/verify.py` are explicitly deferred to the project's
Linux sandbox with bubblewrap before merge. This host is Windows; `wsl --list --quiet`
reports only the managed Docker Desktop distribution. No Linux sandbox checks were
disabled or reported as passed. No frozen defense files or historical runs were edited.
