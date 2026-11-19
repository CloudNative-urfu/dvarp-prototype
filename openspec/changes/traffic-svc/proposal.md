# Why

Issue #36 replaces the in-process legal traffic adapter behind the contract agreed in
#19 with an independent HTTP service. The service must consume the existing snapshot
and manifest (#38), preserving schedules, client dialogue and experiment outcomes.

# What Changes

- Add `traffic_svc` on loopback using svckit and a `SvcTraffic` factory for `TrafficPort`.
- Verify snapshot identity/hash, the manifest and the restored episode before binding.
- Keep scheduling formulas and ResponsiveUser semantics in the shared in-process core.
- Use one CSV-backed physical snapshot per seed across service-run configurations.
- Confine defender SQLite work to a single worker thread; a lock alone does not preserve
  SQLite thread affinity across HTTP requests (existing parity test failed on Windows).
- Update lifecycle/traceability and add HTTP parity/failure tests to dev_smoke.

# Capabilities

- `traffic-service`: implement the existing contract, without changing canon or formulas.

# Impact

Service CLI selects `SvcTraffic`; legacy `Runner` retains `CorpusTraffic`. Defense
directories, frozen configurations and archived revisions are unchanged. No dependencies.
