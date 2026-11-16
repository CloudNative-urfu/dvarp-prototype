## ADDED Requirements

### Requirement: Defender as an HTTP service
The repository SHALL provide `darp_range/defender_svc/` — an HTTP service on 127.0.0.1
(`svckit`, stdlib only) holding the staged frozen configuration, guards, reaction policy,
process rules, tools, agent, LLM client and the executor with the episode DB. The executor
SHALL be the only writer of the episode DB during the episode. The in-process adapter SHALL
execute the same defender core (`defender_svc/core.py`).

#### Scenario: Service serves a defender turn
- **WHEN** the orchestrator posts a channel message to `/episode/respond`
- **THEN** the service runs the guards, the agent and its tool calls against the executor
  and returns the reply, `closed`, signals and the raw events of the turn

### Requirement: Raw records only, orchestrator writes the log
Each call SHALL carry the orchestrator's new events; the service SHALL write its components'
events into a mirror of the flow log and return only the new raw events. The orchestrator
SHALL append them in order and SHALL treat a `seq` mismatch as a service failure. The
service SHALL NOT aggregate or compute final metrics.

#### Scenario: Records land in order
- **WHEN** a call completes
- **THEN** the orchestrator log holds the service events at the same `seq` they had in the
  service, and the episode trajectory equals the in-process one

### Requirement: Staged frozen config
The orchestrator SHALL stage a copy of the frozen configs (JSON + lock) per config cell. The
service SHALL verify it (`load_frozen`, defense-code hashes) before binding the port and
SHALL exit non-zero without `ready.json` on drift; it SHALL run `FreezeGuard.check` on the
assembled defender before each episode.

#### Scenario: Drift refusal
- **WHEN** a staged config file differs from the lock
- **THEN** the service exits with code 3 and the orchestrator marks the cell `refused`

### Requirement: Fail-closed across the boundary
A transport failure SHALL be retried once with the same request id, the service answering
from its saved response, and the retry SHALL be logged as `runner.svc_retry`. A second
failure, a non-200 answer or a dead process SHALL end the episode with status `svc-error`;
later episodes of the flow SHALL be `svc-error` without calls. A defender exception SHALL end
the episode exactly as in-process.

#### Scenario: Service dies mid-flow
- **WHEN** the service process exits during a flow
- **THEN** the current and later episodes are recorded as `svc-error` and the flow completes

### Requirement: Transport overhead accounting
Service-mode flow costs SHALL carry `defender_borne_transport` (requests, retries, payload
bytes, hop wall time without in-service handling, handling time), separate from guard and
agent costs and excluded from parity.

#### Scenario: Transport line visible
- **WHEN** a service-mode run is reported
- **THEN** each flow and `run.json` show the transport line

### Requirement: Parity gate
An offline service-mode flow (StubBackend, same seed, corpus slice and config) SHALL reproduce
the in-process flow: episode outcomes, final DB hashes, trajectories, guard/policy decision
sequences and defender/attacker token and request aggregates.

#### Scenario: Parity reproduces the reference
- **WHEN** `tests/test_defender_svc.py` runs both modes
- **THEN** all compared values are equal

### Requirement: Import isolation
The defender-svc package SHALL NOT import `attackers`, `isolation`, `corpus`, `evaluator`,
`metrics` or the runner, checked statically and by the live service's loaded modules.

#### Scenario: Live service modules
- **WHEN** `/health` is queried after a flow
- **THEN** none of those packages is loaded in the service process
