# Verification — 2026-11-19

Base: develop 91274e0 (documentation refresh over e3a3ef5); branch: Issue-#36.
Python 3.12.16, Windows.
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

## Full unit suite and baseline comparison

`python -m unittest discover -s tests -t .` completed: **1001 tests in 2399.165 s**,
**17 failures, 15 errors, 31 skips**. This result is not reported as green.
No traffic-svc test failed. Full log: `runs/issue-36-validation/full-tests.log`.

Every failing test method was rerun in a clean detached worktree at develop 91274e0:
all **24 distinct failing methods** reproduced their failure there. Both failed-import
modules (`test_rev11_payout_recovery`, `test_rev11_shop_slice`) also failed independently
on clean develop with the same missing `signal.SIGKILL` error. No newly failing test
method was identified relative to this baseline. Error totals can differ because one
test can also fail during Windows temporary-file cleanup.

The existing failures involve Windows open-file/read-only cleanup assumptions in corpus
and append-only tests, frozen-corpus/hash checks, and legacy live/replay scenarios returning
exit 2. These failures have not been fixed or skipped as part of #36. Baseline evidence:
`runs/issue-36-validation/baseline-failures.log`; comparison script/result are alongside it.

Full `scripts/verify.py` remains explicitly deferred to the project's Linux sandbox with
bubblewrap before merge. This host is Windows; `wsl --list --quiet` reports only the managed
Docker Desktop distribution. No Linux sandbox checks were disabled or reported as passed.
No frozen defense files or historical runs were edited.
