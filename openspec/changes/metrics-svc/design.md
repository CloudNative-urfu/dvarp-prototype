# Design: metrics-svc

## Context

- `darp_range.metrics` reads a runner run dir (`manifest.json`, `run.json`,
  `results/<config>.json`) via `load_run`, computes with `compute(RunData, reference, l_viol)`
  and writes `metrics.json`, CSV tables and `report.md` via `write`. It imports only
  `event_log` (rates, USD conversion) and `manual_queue` (analytic queue formulas); its CLI
  demo (`metrics/__main__.py`) is the only import of the runner.
- `report_cli` runs the pipeline under `no_network()` (loopback TCP is blocked too).
- Per-turn telemetry of the defender side (guard signals, policy decisions, costs) is in the
  flow logs `logs/<config>/b<k>-<variant>.jsonl` (#19 keeps them as raw records).

## Decisions

- **Service hosts `darp_range.metrics` unchanged.** Formulas are not edited or copied; the
  service is the entry point. Byte-identity of `metrics.json` with the in-process computation
  is the test of "no formula change". `RunData.source` is the client's run dir path.
- **Contract.** `POST /runs` {run_id, manifest, run, source} (start), `POST /runs/<id>/results`
  {config, doc}, `POST /runs/<id>/log` {path under `logs/`, lines} (chunked stream),
  `POST /runs/<id>/finish` {reference, l_viol} → {files: name → text, gates_pass,
  marked_g_tau}; `GET /health`. Names are validated; a run id cannot be reused.
- **Telemetry check.** On finish the service verifies the hash chain of every received log
  (422 `telemetry_invalid` otherwise). The СС keeps its own smoke before sending
  (`instrument_smoke`: chains valid, every episode has an evaluator verdict with the DB-state
  diff fields).
- **G-tau** (definition fixed here, canon dvarp#16 p.4: a forced escalation of a legal case
  counts as FP): a *false interrupt* is a legal episode of a clean (`base`) flow in which the
  reaction policy chose `isolate`, `escalate` or `terminate`. Rate per config with Wilson 95 %
  CI; the gate passes when the rate ≤ τ = 5 %; a failing config is *marked*, not excluded.
- **Honest accounting.** Per config: every flow's split-accounting checks pass; both sides
  reported; base flows carry no attacker cost; the defender-svc transport line
  (`defender_borne_transport`, #19), when present, is complete and not inside `defender_borne`.
- **Independent L\*.** `metrics_svc.gates.lstar_check` (moved from acceptance FR-1.24 with
  the §9.2 status rules); acceptance calls it.
- **Determinism.** The client starts the service with `PYTHONHASHSEED=0`.
- **Process.** `svckit.ServiceProcess` (spawn `python3 -m …`, wait for `ready.json`,
  `pids.json`, SIGTERM → kill); `runner.svc.DefenderService` uses it as well.
- **Offline pipeline.** Under `--metrics-svc` the runner seeds still run under
  `no_network()`; the metrics calls happen after that block.

## Non-Goals

- Moving telemetry out of the СС; computing metrics for service-mode (legal-only) runs — the
  formulas need the paired attacked flows, which come back with the attack-side integration.
