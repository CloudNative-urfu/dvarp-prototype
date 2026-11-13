## ADDED Requirements

### Requirement: Research matrix configuration set
The preregistered configuration set SHALL follow `guardrails-research-design.md` §4: baselines
`g0` and `g_proc` only (no `g_simple`, canon deviation FR-1.12); a positional matrix of the
builtin guards at input, context, tool, output and all four layered, each under the three canon
reaction policies `canon_isolate`, `canon_escalate`, `canon_terminate`; the OSS input detector
`g_in_oss` at a fixed reaction; controls `g0_seed2` and `g_in_detect_only`; leave-one-out
variants of `g_layered`. The set MUST be frozen by the project's freeze procedure
(`python3 -m darp_range.configs freeze --replace`), never by editing hashes.

#### Scenario: Frozen set verifies
- **WHEN** `python3 -m darp_range.configs verify --dir configs` runs on the repository
- **THEN** it prints `OK` and the lock lists exactly the 24 preregistered configurations

#### Scenario: Policies differ only in the flag reaction
- **WHEN** the three variants of one placement are compared
- **THEN** their guard sets are identical and the `flag` reaction is `isolate`, `escalate`,
  `terminate` respectively, with `block` → `terminate` in all three

#### Scenario: Structured-form baseline rejected
- **WHEN** a configuration with family `g_simple` or defender `simple` is loaded
- **THEN** loading fails with `ConfigError`
