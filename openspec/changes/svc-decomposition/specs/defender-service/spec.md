## ADDED Requirements

### Requirement: Defender as an HTTP service
The repository SHALL provide `darp_range/defender_svc/` — an HTTP service bound to 127.0.0.1
(built on `svckit/http.py`, stdlib only) that holds the staged frozen guard configuration,
the guard pipeline, the reaction policy, escalation rules, the agent and the LLM client,
while the orchestrator remains the single owner of the executor, the episode DB, snapshots,
the evaluator, metrics (incl. `E_L`/`E_A` and the analytic queue block) and the report.

The service code SHALL NOT modify frozen defense modules
(`darp_range/{configs,guard_contract,guard_plugins,guard_policy,executor,tools}/`).

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
in its responses for the orchestrator to write in order. The service SHALL carry passive
internal instrumentation — collectors subscribing to the same raw events (guard journal,
costs, decisions, escalation/clarify events) — that does not affect agent behavior; only raw
records with episode/turn ids leave the service, and the traffic-class attribution
(`E_L` natural+FP vs `E_A`) is performed by the orchestrator.

#### Scenario: Records land in order
- **WHEN** a turn completes across both processes
- **THEN** the orchestrator writes the returned records into its event log and meters
  without service-side writes

#### Scenario: Instrumentation stays passive
- **WHEN** a collector observes an event inside the service
- **THEN** the agent loop behavior is unchanged, and the collector produces raw records only

### Requirement: Two isolated launch contours in the orchestrator
The orchestrator SHALL be structured as a thin pair-scheduling core plus two strictly
separated launch contours — defender-side (config staging, service spawn, per-episode freeze
check, and driving the legal traffic through the traffic-svc contract) and attack-side (the
polygon connection contract: a start-of-attack-cycle signal with the episode invite — config,
seed, budget — and a completion signal with the raw records of the attacking side) — with no
mutual imports and no shared mutable state; the channel SHALL be the only mediator between
the contours. The traffic-svc contract SHALL be described explicitly: an episode/flow invite
carries config, seed, budget and the id + hash of the DB snapshot. Until the polygon service
exists, the current attacker (bwrap + JSON-lines IPC) SHALL run in a transitional mode behind
the same contour; until the traffic service exists, the current simulation internals SHALL
run behind the same traffic-svc contract interface.
Import isolation SHALL be enforced by an architecture test (attack-side does
not import defender-side modules and vice versa).

#### Scenario: Contours do not cross-import
- **WHEN** the architecture test inspects the orchestrator's launch contours
- **THEN** no module of one contour imports a module of the other; shared access is limited to
  channel types and the port interfaces

#### Scenario: Telemetry stays invisible to the attacking agent
- **WHEN** a turn completes and per-turn telemetry (guard triggers, on-the-spot diff verdicts,
  costs) is transferred next to the dialog reply
- **THEN** the telemetry travels in a separate stream, is not visible to the attacking agent,
  and is consumed by the metrics service (issue #33), not by the orchestrator's defender side

#### Scenario: Legal episodes bind to real DB state
- **WHEN** the traffic service opens a legal episode against a snapshot
- **THEN** the episode references real rows of the generated order base (orders, positions,
  tickets consistent with rules R1–R7 and the corpus ground truth), the invite carries the
  snapshot id + hash, every configuration of the run starts from the same snapshot, and
  placeholder identifiers are rejected

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
