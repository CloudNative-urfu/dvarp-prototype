# Design: svc-decomposition

## Context

- Before: `runner/flow.py` `_EpisodeRun` restored the snapshot, bound the corpus entry,
  applied the fixture, built `Executor`, called `configs.assemble_defender` +
  `FreezeGuard.check`, ran `TextChannel.run_session` and evaluated the DB; every component
  wrote into one in-process `EventLog` (hash chain, global `seq`).
- Defender-side code reads the log during an episode (`configs/process.py` counts agent
  tokens of the episode), so the service needs the flow's events up to the current point.
- Building blocks: `svckit/http.py` (127.0.0.1, atomic `ready.json`, `call()` distinguishes
  server answers from transport failures), `shop_svc` process pattern.
- Constraints: stdlib only (`runtime_requirements_empty`, `import_audit`); in-process path
  stays the reference; `console/` untouched. Defense code may be edited (current freeze is
  ignored during development, #45/#47/#48) — this change does not need to.

## Ownership (issue #19, 2026-11-16)

| Component | Owns |
| --- | --- |
| defender-svc | config staging check, guards, policy, process rules, tools, agent, LLM client, executor + episode DB writes, passive raw-record collection |
| orchestrator | config + seed, staging, spawn/lifecycle, snapshots, restore + hash check, fixture deploy, EventLog (sole writer), evaluator call, flow accounting |
| traffic-svc (contract) | schedule of legal arrivals, binding to real snapshot rows, text-only client |
| metrics-svc (#33) | aggregation and final metrics from raw telemetry |

Code mapping: `runner/` → orchestrator; `agent`, `tools`, `guard_*`, `executor`, `llm` →
defender-svc (`defender_svc/core.py` uses them; `runner/inproc.py` runs the same core
in-process); `channel` — text barrier on the orchestrator side; `state_store` — shared
(generator/snapshots/deploy — orchestrator, episode writes — executor); `runner/flow.py`
legal client + `plan.build_schedule` + `corpus.bind` — traffic internals behind
`TrafficPort` (`runner/legal_traffic.py`) until #36; `attackers`, `isolation`, `corpus`,
`agent_demo`, `manual_queue`, `pay_emu`, `shop_svc` — out of scope.

## Decisions

- **Ports.** `FlowContext` gets port factories (`defender_port`, `traffic_port`,
  `attack_slot`); defaults are the in-process adapters registered by the composition root
  `runner/__init__.py`, so `flow.py` imports only contracts. The attack slot moved to
  `runner/attack.py` and is used only by the legacy `Runner`.
- **One defender core.** `defender_svc/core.py` (`DefenderFlow` per flow: model, process
  ledger, boundary, FreezeGuard; `DefenderEpisode`: executor, assembled defender, transcript)
  is executed both in-process (`runner/inproc.py`) and inside the service.
- **Raw-record protocol (single writer).** Each call carries the orchestrator's new events
  (delta); the service keeps a mirror log (`defender_svc/mirror.py`), its components write
  into it under the normal schema checks, and the call returns only the new events. The
  orchestrator re-appends them through its writers in the same order and checks `seq`; the
  hash chain is recomputed by the orchestrator. Defender exceptions return as 200 +
  `raised` (type, message, `fatal`) so the episode ends exactly as in-process.
- **Endpoints.** `GET /health`; `POST /flow` (run id, research mode, seed, model mode,
  profile, boundary), `/episode/open` (`EpisodeInvite`: id, DB path, actor, ticket, arrival,
  order), `/episode/defender` (assemble + `FreezeGuard.check`), `/episode/record` (ToolPort),
  `/episode/respond`, `/episode/release`, `/episode/close`. Bodies carry `req` + `events`.
- **Lifecycle.** One process per config cell: staged copy of `configs/*.json` + lock;
  `python3 -m darp_range.defender_svc --config-dir … --config … --ready …`; the service
  runs `load_frozen` + `FreezeGuard` before binding (port 0) and exits 3 on drift; the
  orchestrator marks the cell `refused`. Registry `svc/pids.json`, stop at cell end.
- **Fail-closed.** Transport failure → one explicit retry with the same `req` (the service
  answers from its saved response, no double execution) and a `runner.svc_retry` event in
  the episode; second failure, non-200 or a dead process → episode `svc-error`, later
  episodes of the flow fail fast as `svc-error`. Per-episode freeze mismatch in the
  service stops the run (`DefenderFrozen`, as `FrozenConfigError` in-process).
- **Honest accounting.** Flow costs gain `defender_borne_transport` (requests, retries,
  payload bytes, hop wall time minus in-service handling time, handling time); never merged
  into guard/agent costs, absent in-process, excluded from parity.
- **Live mode.** The service builds the agent model from the plan (`agent_mode`,
  `agent_profile`); live credentials come from the inherited `RESEARCH_LLM_*` environment.
  Custom model factories and `guard_fault_rate` stay in-process only.
- **Service mode runs legal traffic only** (`variant="base"`); the mix run (dvarp#20) waits
  for the attack-side integration. No placeholder for the attack contour is designed here.

## Risks / Trade-offs

- The mirror log relies on `EventLog` internals (`_events`, `_episodes`); a change in the
  log's constructor needs the mirror updated — covered by the parity test.
- Per-call event deltas cost bytes and time; measured in the transport line.
- `plan.py` still imports `attackers`/`manual_queue` for validation and scheduling; the
  orchestrator core imports only its data types (`RunPlan`, `BlockSchedule`, `FlowSlot`).
