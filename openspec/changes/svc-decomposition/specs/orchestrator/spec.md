## ADDED Requirements

### Requirement: Episode flow over ports
`runner/flow.py` SHALL reach the defender side only through `DefenderPort`/`DefenderSession`
(+ `ToolPort`) and the legal traffic only through `TrafficPort` (`runner/ports.py`). Default
adapters SHALL be the in-process reference, wired by `runner/__init__.py`.

#### Scenario: Orchestrator imports only contracts
- **WHEN** the architecture test reads the imports of `runner/flow.py`, `ports.py`, `svc.py`
- **THEN** none imports defender internals (`agent`, `tools`, `guard_*`, `executor`, `llm`,
  `defender_svc`), traffic internals (`corpus`, `manual_queue`, `attackers`, `isolation`), the
  in-process adapters or `build_schedule`; from `configs` only config data names

### Requirement: Single-contour service-mode orchestrator
`python3 -m darp_range.runner svc` SHALL, per selected frozen config: compute the config seed,
build the snapshot, invite the traffic (`FlowInvite`), spawn one defender-svc, run the legal
flows of each block (`variant="base"`), write `logs/`, `results/<config>.json` and `run.json`,
and stop the service. Attack traffic is not run in service mode.

#### Scenario: Clean lifecycle
- **WHEN** a service-mode run finishes
- **THEN** `svc/pids.json` lists no running service and every legal episode is evaluated
