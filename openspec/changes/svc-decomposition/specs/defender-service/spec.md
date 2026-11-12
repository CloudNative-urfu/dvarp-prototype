## ADDED Requirements

### Requirement: Defender as an HTTP service
The repository SHALL provide `darp_range/defender_svc/` — an HTTP service bound to 127.0.0.1
(built on `svckit/http.py`, stdlib only) that holds the frozen guard configuration, the guard
pipeline, the agent and the LLM client, while the orchestrator remains the single owner of the
executor, the episode DB, snapshots, manual_queue, evaluator, metrics and the report.

The service code SHALL NOT modify frozen defense modules
(`darp_range/{configs,guard_contract,guard_plugins,guard_policy,simple_baseline,executor,tools}/`).

#### Scenario: Service serves a defender turn
- **WHEN** the orchestrator posts a turn to the service session
- **THEN** the service runs the guard pipeline and agent loop, executes tool calls through the
  orchestrator's tool callback, and returns the reply, guard journal, model usage and agent
  events

### Requirement: Staged frozen config
The orchestrator SHALL stage the frozen config directory per service process and pass it via
`DARP_SVC_CONFIG_DIR`. The service SHALL verify the config and defense-code hashes itself
(`load_frozen` + `FreezeGuard.check`) and SHALL refuse to serve on any drift, exiting non-zero
before binding the port.

#### Scenario: Drift refusal
- **WHEN** a config or defense-code file hash differs from `configs.lock.json`
- **THEN** the service exits non-zero before serving, and the orchestrator reports the cell
  as failed rather than running it

### Requirement: Tool callback contract
Tool calls intercepted inside the service SHALL be forwarded to the orchestrator's
`/tool-callback` data API (`{episode_id, turn_id, seq, tool_name, stage, args}`) and executed
there through the toolbox semantics on the episode DB. The in-process `ToolInterceptor`
contract (guard decision before the call crosses the boundary) SHALL be preserved.

#### Scenario: Guard blocks before the boundary
- **WHEN** a guard decides `flag` or `block` on a tool call inside the service
- **THEN** the call is not forwarded to the callback, and the decision is recorded in the
  returned guard journal

### Requirement: Single-writer event and cost aggregation
The orchestrator SHALL be the only writer of the EventLog and the episode DB. The service
SHALL return per-turn records (guard journal, model usage, agent events, sequence numbers)
in its responses for the orchestrator to write in order.

#### Scenario: Records land in order
- **WHEN** a turn completes across both processes
- **THEN** the orchestrator writes the returned records into its event log and meters
  without service-side writes

### Requirement: Meter scope across the boundary
The service SHALL receive a scoped meter budget (run/cell/episode ids and limits) at session
start and SHALL apply `meter.admit` before each model call, returning per-call usage records;
the orchestrator SHALL aggregate them into the existing meter chain.

#### Scenario: Budget enforced inside the service
- **WHEN** a model call would exceed the scoped limit
- **THEN** the service refuses the call exactly as the in-process meter would

### Requirement: Fail-closed across the boundary
A service crash, connection timeout or non-200 response SHALL abort the episode with status
`svc-error`, recorded honestly in the report; no silent skip. A single explicit retry SHALL
be visible in the event log.

#### Scenario: Service dies mid-episode
- **WHEN** the service process exits or the request times out
- **THEN** the episode is recorded as `svc-error` and appears in the report accounting

### Requirement: Honest accounting of transport overhead
The report SHALL carry a separate defender-borne bucket for HTTP transport overhead (bytes,
wall time of orchestrator↔service hops), SHALL NOT merge it into guard or agent costs, and
L* parity SHALL be evaluated within a declared tolerance.

#### Scenario: Transport bucket visible
- **WHEN** a service-mode run is reported
- **THEN** the transport overhead is shown as its own line and L* is compared against the
  in-process reference within the declared tolerance

### Requirement: Parity gate
An offline service-mode run with `StubBackend` on the same seed, corpus and frozen config
SHALL reproduce the in-process run: identical episode outcomes (final state hashes), identical
guard decision sequences, and equal cost aggregates within the transport bucket, verified by a
dedicated parity test.

#### Scenario: Parity reproduces the reference
- **WHEN** the parity test runs in both modes
- **THEN** outcomes, guard decisions and aggregates match (transport bucket excluded), and the
  legacy in-process marking can be updated in the docs

### Requirement: Lifecycle and stdlib-only
The orchestrator SHALL own the service lifecycle: staging, spawn (port 0 → argv),
`ready.json`, a `pids.json`-style registry and shutdown at run end. The service SHALL not add
any runtime dependencies (`runtime_requirements_empty`, `import_audit` must keep passing).

#### Scenario: Clean lifecycle
- **WHEN** an orchestrated run finishes or is interrupted
- **THEN** service processes are terminated and the registry reflects that no service is left
  running
