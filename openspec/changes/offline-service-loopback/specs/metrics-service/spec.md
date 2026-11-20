## MODIFIED Requirements

### Requirement: СС calls the service
`report_cli offline|live` SHALL compute all contour metrics through a metrics-svc process
on 127.0.0.1: the service starts before the offline network-ban block; runs and metric
computation happen inside the block, with connections to the service allowed only through
the loopback allowlist (port from the service's ready.json). In-process computation
(`metrics_svc=False`) SHALL be rejected. Every allowed connection SHALL be recorded in
`contour.json` (per service/host/port with connection counts); everything else stays
blocked (`NetworkBlocked`).

#### Scenario: Instrument smoke stays in the СС
- **WHEN** an episode lacks the evaluator's state-diff verdict or a log chain is broken
- **THEN** the smoke fails before any telemetry is sent

#### Scenario: In-process computation refused
- **WHEN** a contour is requested with `metrics_svc=False` (or `--no-metrics-svc`)
- **THEN** the pipeline fails before any run starts

## ADDED Requirements

### Requirement: Own-service loopback allowlist
The offline network ban SHALL accept an allowlist `service name → port` taken from own
services' `ready.json`. Only loopback connections to allowlisted ports SHALL pass; each
allowed connection SHALL be recorded, and any other connection (non-loopback or
non-allowlisted port) SHALL be blocked and recorded as an attempt.

#### Scenario: Own service allowed, outside blocked
- **WHEN** the offline contour computes metrics through its metrics-svc under the ban
- **THEN** the service connections are recorded in `contour.json` (`allowed`), while any
  connection to another port or a public address raises `NetworkBlocked`
