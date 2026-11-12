# Proposal: svc-decomposition

## Why

The zero-version scaffold runs the defense in a single process: `Runner` owns runner,
channel, agent + tools, executor, state_store, manual_queue, metrics and the LLM client
in-process; only the attacker (bwrap + JSON-lines IPC) and the shop/pay contour run
outside. The team decided (issue #19) to move the defensive part into a service, per the
project direction in `docs/brief.md`: data through versioned local APIs, vertical slices,
service decomposition (defender service / attacker service / script orchestrator). The
detailed design below fixes the boundary so later decomposition steps reuse it.

## What Changes

- New package `darp_range/defender_svc/`: HTTP service on 127.0.0.1 (reuse `svckit/http.py`)
  holding the frozen config, the guard pipeline, the agent and the LLM client.
- Orchestration script stays the owner of: executor, episode DB, snapshots, manual_queue,
  evaluator, metrics, corpus, attacker isolation, report. Tool calls of the defender are
  executed by the orchestrator's data API; guards intercept the call inside the service
  before it crosses HTTP back to the orchestrator.
- Seams in the runner flow: `DefenderPort` / `ToolPort` interfaces with in-process adapters
  (Phase 0), service adapters (Phase 1).
- Legacy in-process path is kept until the parity gate passes (same seed/corpus/config →
  identical outcomes, guard decisions and cost aggregates).
- Honest accounting gains a separate transport-overhead bucket (defender-borne).

## Capabilities

### New Capabilities
- `defender-service`: the defender as an HTTP service with a tool-callback contract,
  staged frozen config, per-episode freeze checks, meter/event replay protocol and the
  parity gate.

### Modified Capabilities
- `runner`: pair-run orchestration gains service-mode adapters behind `DefenderPort`/`ToolPort`
  seams; in-process path unchanged as the parity reference.
