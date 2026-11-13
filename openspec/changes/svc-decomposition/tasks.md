# Tasks: svc-decomposition

## 1. Phase 0 — spec alignment (done on branch before implementation)

- [x] branch rebased onto current `develop` (post-#29: corpus v2, 24 configurations, 3 canon
      reaction policies, `E_L`/`E_A`, analytic queue block, no `simple_baseline`, no DES)
- [x] spec rewritten: stale references removed (simple_baseline, manual_queue DES, corpus v1,
      19 configurations), owner vision (three services, two orchestrator contours, frozen-DB
      snapshot contract) recorded

## 2. Phase 1 — seams

- [ ] seams `DefenderPort` (defender turn interface) and `ToolPort` (tool execution interface)
      extracted from `runner/flow.py`; in-process adapters preserve current behavior; adapters
      live only outside frozen defense code (`tools/`, `configs/` are not edited)
- [ ] existing unit tests green (`ruff check .`, module-targeted unittest runs); no changes to
      frozen defense code (`darp_range/{configs,guard_contract,guard_plugins,guard_policy,
      executor,tools}/`)

## 3. Phase 2 — defender-svc + two-contour orchestrator + parity

- [ ] package `darp_range/defender_svc/`: server (svckit HTTP on 127.0.0.1), session
      (frozen config staging + hash checks + per-episode `FreezeGuard.check`), `/start`,
      `/turn`, `/finish`, `/health`
- [ ] passive internal instrumentation: collectors subscribe to the same raw events (guard
      journal, costs, decisions, escalation/clarify events) without affecting agent behavior;
      raw records returned per turn with episode/turn ids (traffic class is attributed by the
      orchestrator)
- [ ] tool callback client in the service + `/tool-callback` data API on the orchestrator
      side (executor op via toolbox semantics, `PendingCall` contract preserved)
- [ ] orchestrator: thin pair-scheduling core + two strictly separated launch contours
      (defender-side: staging, spawn, freeze check; attack-side: corpus brief, sandbox spawn,
      attempt lifecycle) — no mutual imports, no shared mutable state, channel is the only
      mediator; architecture test enforces import isolation (attack-side does not import
      defender-side and vice versa)
- [ ] orchestrator-side lifecycle: staging, spawn (port 0 → argv), ready.json, pids.json
      registry, shutdown; single-writer rule (svc returns records, orchestrator writes)
- [ ] meters: scoped meter budget at `/start`, per-turn cost records, aggregation into
      `RunMeter`/`ChainedMeter`/`ScopedMeter` unchanged; `E_L`/`E_A` computed from raw
      records by the orchestrator; analytic queue block (`λ_Q → W_q/Q_L → SLA probability`)
      stays orchestrator-side
- [ ] parity test: service-mode offline run (StubBackend, same seed/corpus/config) reproduces
      the in-process run — identical episode outcomes (final state hashes), guard decision
      sequences, cost aggregates within the transport bucket
- [ ] fail-closed behavior: svc crash / timeout / non-200 → episode status `svc-error`
      recorded honestly; single explicit retry visible in the event log

## 4. Phase 3 — live mode + accounting

- [ ] live mode through the service (`RESEARCH_LLM_*` passed via env); migration of
      `scripts/live_mode_a_v.py` to the service path
- [ ] honest accounting: transport-overhead bucket (bytes, wall time of HTTP hops) as a
      separate defender-borne line in the report; L* parity tolerance documented
- [ ] `scripts/verify.py`: in-process path unchanged + service-mode smoke step
- [ ] docs: legacy marking of `Runner`/`report_cli offline` updated after parity passes;
      `docs/traceability.md` rows touched by this change updated

## 5. Acceptance

- [ ] parity gate passes (see Phase 2 test)
- [ ] architecture test passes: orchestrator contours do not import each other
- [ ] `runtime_requirements_empty` and `import_audit` still pass (stdlib-only)
- [ ] svc refuses to start on config drift; per-episode freeze check inside the service
- [ ] acceptance checks of `report_cli` untouched on the in-process path
- [ ] ruff + unit tests green; PR to `develop` with `Closes #19`
