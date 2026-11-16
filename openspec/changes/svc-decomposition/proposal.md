# Proposal: svc-decomposition

## Why

The zero-version scaffold runs the defense in the orchestrator's process: `runner/flow.py`
builds the executor on the episode DB, assembles the defender from the frozen config and
drives the channel, all against one in-process EventLog. Team-lead decisions (issue #19,
2026-11-15/16): the attacking side leaves the prototype (developed separately, wired later
through the dvarp#20 contract); the СС consists of **defender-svc** (the refund service with
the agent and the business process), a **single-contour orchestrator** (defender-side, legal
traffic only) and **traffic-svc** (legal refund traffic, contract only here, internals #36).
Final metrics move to metrics-svc (#33).

## What Changes

- New package `darp_range/defender_svc/`: HTTP service on 127.0.0.1 (`svckit`) holding the
  staged frozen config (lock + defense-code check at start, `FreezeGuard.check` before each
  episode), guards, reaction policy, process rules, tools, agent, LLM client **and the
  executor with the episode DB** (the executor is the only writer of the episode DB).
  Only raw records leave the service: the events its components write, returned per call.
- `runner/ports.py`: contracts `DefenderPort`/`DefenderSession` (+ `ToolPort`: harness-side
  executor operations) and `TrafficPort` (flow invite with config, seed, budget, snapshot
  id + hash → legal episodes bound to real snapshot rows → completion signal).
- `runner/flow.py` reaches the defender and the traffic only through the ports; in-process
  adapters (`inproc`, `legal_traffic`, `attack`) keep the reference behaviour; the
  in-process `Runner` (paired base/attacked) is the legacy research path.
- `runner/svc.py` + CLI `python3 -m darp_range.runner svc`: single-contour orchestrator —
  config choice + seed, staging, spawn of one defender-svc per config cell, snapshots and
  fixture deploy, EventLog writing, legal traffic through the traffic contract.
- Parity gate, fail-closed transport, transport overhead as a separate defender-borne line,
  architecture import test.

## Capabilities

### New Capabilities
- `defender-service`: the defender (with executor and episode DB) as an HTTP service;
  raw-record protocol; staged frozen config; fail-closed; parity gate.
- `orchestrator`: single-contour service-mode orchestrator over the ports.
- `traffic-service`: contract of the legal-traffic service (implementation — #36).
