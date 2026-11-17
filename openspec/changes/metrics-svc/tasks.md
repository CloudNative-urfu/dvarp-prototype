# Tasks: metrics-svc

- [x] `svckit.ServiceProcess`; `runner.svc.DefenderService` on top of it
- [x] `metrics_svc` package: contract, telemetry store, chain check, compute + write, gates
- [x] gates: G-tau (Wilson CI, marking), honest accounting (incl. transport line), independent
      L* (`lstar_check`, moved from acceptance)
- [x] СС adapter `metrics_svc.client` and `report_cli --metrics-svc`; СС smoke
      `instrument_smoke`
- [x] tests: byte-identity with in-process metrics, gates, telemetry rejection, import isolation
- [x] docs: `docs/manuals/metrics-svc.md`, `docs/traceability.md` (FR-1.23/1.24, NFR-2.9,
      NFR-2.14), `docs/repo-map.md`
- [x] `ruff check .`, `scripts/dev_smoke.py` green; PR to `develop` with `Closes #33`
