# Tasks — issue #36

- [x] Implement HTTP TrafficPort using the existing scheduling/binding/customer core.
- [x] Verify snapshot identity/hash and manifest; bind read-only to real orders.
- [x] Wire service CLI; share one CSV-backed snapshot per seed across configurations.
- [x] Add terminal signals, exact retries and process cleanup.
- [x] Fix defender-svc SQLite thread affinity exposed by baseline parity.
- [x] Add parity, budget, binding, invalid hash, manifest, retry and multi-block tests.
- [x] Run ruff, dev_smoke and appropriate regression tests; see verification.md.
- [x] Finalize lifecycle/manual and traceability references.
- [x] Full verify explicitly deferred: native Windows environment; the only installed WSL
  distribution is Docker Desktop's managed backend, not a research Linux sandbox with
  bubblewrap. Full unittest discover / verify must run in the project's Linux environment
  before merge; no sandbox checks are weakened or marked passed here.
