## Context

DARP revision 8 is an offline-verified range for a returns support agent (critique rev8: verify 101/101, 935 tests). The attacker side (`darp_range/attackers`) talks to the defender only via `channel.Observation`, but both run in one process. Probe L1 showed the attacker writer exposes the shared `EventLog`. `llm/settings.py` reads `RESEARCH_LLM_API_KEY` in the same process. `brief_from_episode` copies `objective/drafts` from corpora authored with defence knowledge. bubblewrap 0.12.0 runs unprivileged on the host and blocks network (probe L2). PEP 578 states in-process audit hooks are not a sandbox; bubblewrap README states security is determined by its arguments.

## Goals / Non-Goals

**Goals:**
- An enforced OS boundary for the attacker process with an explicit, test-linked access matrix.
- Evidence that attacker input depends only on the allowed chat history (payload audit + differential invariance).
- Honest claim levels: no black-box claim from legacy corpora, research-mode, or a run whose probes failed.
- Byte-identical behaviour of modes D and A on the stub versus the in-process baseline.

**Non-Goals:**
- Isolating the defender side; persistent memory (level B); version league (level C).
- Live Qwen runs, guardrail effect measurement, L* on v2r, changes to server rules / legal client.
- New attacks, blind held-out sets, text filters for leakage.

## Decisions

1. **bubblewrap over in-process hardening or plain subprocess.** In-process measures are bypassable (`gc`, frames); a plain subprocess of the same uid can read project files and, without a PID namespace, possibly `/proc/<ppid>/environ` (proc(5): ptrace access check). Both are kept only as negative controls for probe sensitivity.
2. **Broker in the parent.** The parent owns channel, LLM client with key, event log and payload audit. The child receives only an attack-only input file (brief, own limits, own memory) and IPC messages. Process granularity: one child per campaign.
3. **Mount policy.** `--unshare-all --die-with-parent --new-session --clearenv`; read-only bind of the interpreter and a staged copy of the attacker package only (no other `darp_range` packages, no project root, no runs, no corpus GT); tmpfs for scratch; no host sockets bound. `/nix` read-only is allowed as system binaries and recorded in the matrix.
4. **IPC schema.** Canonical JSON lines; parent→child: `observation`, `llm_response`; child→parent: `message|done`, `llm_request(messages)`, `attempt_record`. Unknown fields or types abort the run as a protocol error, not as a defence success.
5. **Payload audit.** The broker records canonical `messages` and sha256 of every attacker LLM request, never headers, URL or key. The audit log is separate from the defender event log.
6. **Claim level computed, not declared.** `isolated_black_box_candidate` only if sandboxed, probes of this run passed, research-mode off, and every brief has non-implementation-informed provenance from the evaluator registry. Provenance lives in a separate registry so corpus MANIFEST hashes do not change.

## Risks / Trade-offs

- Wrong bwrap arguments → mitigated by one probe per deny row and a weak-sandbox control that must fail probes.
- `/nix` store is visible → contains no project data; probe checks that no project path is reachable.
- Overhead of process start and IPC → published; target ≤ +20 % on stub mode A.
- Nondeterminism through IPC → canonical JSON and byte-level regression against the in-process path.
- Hosts without user namespaces → explicit downgrade to `in_process_research`.
- Same Qwen in both roles: process isolation does not make roles independent model families.

## Migration Plan

1. Add the matrix and probes first; record RED on the in-process path (L1) and weak sandbox.
2. Add the sandbox runner behind an option; default remains in-process until byte-level regression passes.
3. Switch mode A/D offline runs to the sandbox only for new coevolution runs; historical scripts and verify keep the in-process path with the `in_process_research` label.
4. Rollback: remove the option; no data migration, corpus hashes unchanged.
