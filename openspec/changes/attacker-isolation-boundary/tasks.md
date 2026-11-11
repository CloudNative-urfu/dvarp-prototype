## 1. Tasks

- [x] access-matrix Add configs/access_matrix.json with validation test linking every attacker deny row to an existing OS probe and every allow row to a positive test; key broker-only; v2r current, boundary-1 legacy; fix event_log docstring
- [x] attacker-sandbox-ipc Run attacker modes D/A in a bubblewrap child with broker IPC; byte-identical stub regression on seeds 7/8/9; no RESEARCH_LLM_* inside child; explicit failure or in_process_research label when sandbox unavailable
- [x] os-negative-probes Provide ≥14 negative OS probes, all DENY in the sandbox, with RED on in-process (EventLog canary) and on a weak same-uid subprocess, plus positive controls; ≤5 min separate command
- [x] payload-audit-invariance Record canonical attacker payloads with sha256 and no secrets; equal hashes under 4 hidden changes × ≥3 briefs, differing hash when an agent reply changes; system_prompt_leak_via_reply recorded without filtering
- [x] attack-provenance-gate Register all existing corpora as implementation-informed without hash changes; reject forged provenance; compute claim_level so legacy, research-mode or failed-probe runs are never black-box

Test details (plain text, not tasks): regression covers corpus v1 mode D and stub mode A; probe list follows spec attacker-isolation; weak sandbox = subprocess same uid without namespaces; hidden changes = R5 threshold, corpus GT, agent system prompt, guardrail policy; canaries are synthetic strings only; 0 live Qwen requests.
