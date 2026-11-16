# Tasks: svc-decomposition

## 1. Phase 0 — spec

- [x] branch rebased onto current `develop`
- [x] spec rewritten for the 2026-11-15/16 decisions: attack side removed; executor and
      episode DB inside defender-svc; single-contour orchestrator (legal traffic only);
      traffic-svc contract (`specs/traffic-service`), flagged in #36

## 2. Phase 1 — seams

- [x] `runner/ports.py`: `DefenderPort`/`DefenderSession`/`ToolPort`, `TrafficPort`,
      `EpisodeInvite`, `FlowInvite`, `BoundEpisode`
- [x] `runner/flow.py` uses only the ports; in-process adapters `inproc`, `legal_traffic`,
      `attack`, wired in `runner/__init__.py`; runner tests unchanged and green

## 3. Phase 2 — defender-svc, orchestrator, parity

- [x] `defender_svc/` (`core`, `mirror`, `server`, `__main__`): staging check before bind,
      per-episode `FreezeGuard.check`, raw-record protocol, `/health`
- [x] `runner/svc.py`: staging, spawn, `pids.json`, `SvcDefenderPort`, `SvcRunner`;
      CLI `python3 -m darp_range.runner svc`
- [x] parity test (StubBackend; legal flows + guard decisions on attacked flows)
- [x] fail-closed: explicit retry visible in the log; `svc-error` episodes
- [x] drift refusal; architecture import test (orchestrator → contracts only; defender-svc
      without research modules, statically and by the live service's loaded modules)

## 4. Phase 3 — live + accounting + docs

- [x] live mode wired through the service (`--agent-mode live`, `RESEARCH_LLM_*` inherited);
      not exercised against a live model in this change (offline parity only)
- [x] transport line `defender_borne_transport` per flow and per config in `run.json`
- [x] docs: `docs/manuals/defender-svc.md`, `run-lifecycle.md` refs, `repo-map.md`
      (legacy marking of `Runner`/`report_cli offline` after parity)
- [ ] `scripts/verify.py` service-mode smoke — not added: verify is the frozen-defense
      acceptance of the in-process path; the parity test runs in `dev_smoke`

## 5. Acceptance

- [x] parity gate passes; architecture test passes
- [x] `runtime_requirements_empty`, `import_audit` pass (stdlib only)
- [x] svc refuses to start on config drift; per-episode freeze check inside the service
- [x] `ruff check .` and `scripts/dev_smoke.py` green; PR to `develop` with `Closes #19`
