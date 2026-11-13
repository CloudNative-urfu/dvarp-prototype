# Proposal: svc-decomposition

## Why

The zero-version scaffold runs the defense in a single process: `Runner` owns runner,
channel, agent + tools, executor, state_store, metrics (incl. `E_L`/`E_A` and the analytic
queue block) and the LLM client in-process; only the attacker (bwrap + JSON-lines IPC) and
the shop/pay contour run outside. The owner's target architecture (issue #19, vision
diagram) splits the monolith into three services: **defender-svc** (the refund process with
the AI agent — guards, reaction policy, escalation rules, tools, executor rights), the
**attacker** (already an isolated sandboxed process) and the **script orchestrator** as the
launcher. The goal is an architecture that mirrors the top-level logic, so the defender can
be taken out standalone, and a paper-quality story: not vibecoded, but a checked
architecture with a parity gate.

## What Changes

- New package `darp_range/defender_svc/`: HTTP service on 127.0.0.1 (reuse `svckit/http.py`)
  holding the staged frozen config, the guard pipeline, the reaction policy, escalation
  rules, the agent and the LLM client. Inside the service: passive raw-record collection
  that does not affect agent behavior; only raw records leave the service — writing to the
  EventLog and aggregation (incl. `E_L`/`E_A` and the analytic queue block `λ_Q = λ_L·E_L +
  λ_A·E_A`) stay in the orchestrator. No second source of truth.
- The orchestrator is restructured into **two strictly separated launch contours**
  (defender-side and attack-side) around a thin pair-scheduling core: no mutual imports, no
  shared mutable state; the channel is the only mediator. Import isolation is enforced by an
  architecture test (in the spirit of `import_audit`).
- Tool calls of the defender are executed by the orchestrator's data API; guards intercept
  the call inside the service before it crosses HTTP back to the orchestrator.
- Seams in the runner flow: `DefenderPort` / `ToolPort` interfaces with in-process adapters;
  adapters live only outside the frozen defense code (`tools/`, `configs/` are not edited).
- Legacy in-process path is kept until the parity gate passes (same seed/corpus/config →
  identical outcomes, guard decisions and cost aggregates).
- Honest accounting gains a separate transport-overhead bucket (defender-borne).

## Capabilities

### New Capabilities
- `defender-service`: the defender as an HTTP service with a tool-callback contract,
  staged frozen config, per-episode freeze checks, meter/event replay protocol, passive
  raw-record instrumentation and the parity gate.

### Modified Capabilities
- `runner`: pair-run orchestration is split into two isolated launch contours (defender-side
  / attack-side) behind `DefenderPort`/`ToolPort` seams; the in-process path stays the
  parity reference. Spec aligned with canon dvarp#17/#18: corpus v2 (40 entries, goals
  `break`/`slow`/`burn`), 24 frozen configurations with three canon reaction policies,
  escalation as a neutral outcome, FR-1.20 scripted multi-turn.
