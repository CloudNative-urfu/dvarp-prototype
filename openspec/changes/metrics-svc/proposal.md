# Proposal: metrics-svc

## Why

Scenario change (issue #33): final metrics are computed by a portable service; telemetry stays
in the СС and does not move; the polygon (a future neighbour service) connects to the СС.
Today `report_cli` computes metrics in-process right after the runner, and the report gates
G-tau and honest accounting (from the dropped task #9) are not computed at all.

## What Changes

- New package `darp_range/metrics_svc/` (svckit HTTP on 127.0.0.1): start signal → telemetry
  stream (run manifest and summary, per-config results with episode verdicts, escalations and
  costs, per-turn flow logs) → completion signal → report files.
- The service computes with the unchanged formulas of `darp_range.metrics` (`E_L`/`E_A`,
  analytic queue, ΔNet/L*, cases У1–У4, `queue_unstable`): `metrics.json` and the tables are
  byte-identical to the in-process computation. It does not import СС measurement code.
- Report gates computed by the service: G-tau, honest accounting, and the independent L*
  recomputation (moved from `report_cli.acceptance`, which now imports it from the service).
- Telemetry check in the service (hash chains of the flow logs) and the СС smoke
  "instrument is sound" (`report_cli.pipeline.instrument_smoke`) before sending.
- `report_cli offline|live --metrics-svc`: the СС calls the service by 127.0.0.1.
- `svckit.ServiceProcess`: shared spawn/ready/registry/stop for defender-svc and metrics-svc.

## Capabilities

### New Capabilities
- `metrics-service`: final metrics and report gates as a service over telemetry.
