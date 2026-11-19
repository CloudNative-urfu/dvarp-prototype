## ADDED Requirements

### Requirement: LLM customer behind the client protocol
The traffic service SHALL construct the legal customer per launch config `client.kind`
("scripted" default, "llm" optional). The LLM customer SHALL implement the existing
`UserEndpoint` protocol: the first message is the scripted corpus opening; later messages
are model completions over the dialogue history with the agent reply as user turn.
Model settings SHALL come from the shared live-model driver (`RESEARCH_LLM_*`) or a
deterministic stub profile for offline runs.

#### Scenario: Missing live model settings fail closed
- WHEN a run requests `client.kind = "llm"` (mode live) and `RESEARCH_LLM_*` is absent
- THEN the traffic service refuses before publishing ready.json; the run stops without
  inventing traffic.

### Requirement: Facts-bound prompt, text-only boundary
The customer prompt SHALL be built from corpus `CustomerFacts` only; the customer SHALL
not receive ground truth, guard decisions, or database rows beyond its own facts. Only
channel Observation fields SHALL enter the customer session; only text SHALL leave it.
The defender side SHALL NOT know whether the customer is scripted or model-driven.

#### Scenario: Scripted path unchanged
- WHEN a run uses the default `client.kind = "scripted"`
- THEN the traffic service behaves byte-identically to the pre-#76 path (parity suites
  remain green).

### Requirement: Usage visibility without accounting changes
The customer summary SHALL report model usage (tokens, requests), turn count and model
version without reply text. This usage SHALL travel through the existing `legal_user`
journal event as plain data and SHALL NOT enter the defender/attacker split accounting.

#### Scenario: Usage recorded per episode
- WHEN an LLM-customer episode completes
- THEN the `legal_user` event of that episode contains the model usage and version,
  and the NFR-2.9 accounting checks pass unchanged.
