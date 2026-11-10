## ADDED Requirements

### Requirement: Isolation probes are executed by broker code
Isolation probes of a run SHALL be executed by a probe module that is not part of the attacker's staged package, launched with the same bwrap argv as the attacker. The broker MUST NOT accept the attacker's `hello.self_check` as isolation evidence; it MAY keep it as an informational field.

#### Scenario: Forged self-check does not change verdict
- **WHEN** an attacker process reports a self_check claiming no environment leaks while the launcher is a weak subprocess without namespaces
- **THEN** the broker-run probes report the leaks and the run does not obtain an isolated claim level

#### Scenario: Existing probes remain DENY
- **WHEN** the 19 rev9 negative probes and 3 positive probes run in the bwrap sandbox through the broker probe module
- **THEN** all negative probes are DENY, the positive probes PASS, and the in-process and weak-sandbox controls still show leaks

### Requirement: Probes are sensitive to bwrap hardening flags
The probe set MUST include: visible PID count at most 2; a scan of every readable `/proc/*/environ` for a synthetic broker canary; `CapEff` from `/proc/self/status`. The broker MUST additionally observe from the host side that the attacker's session id differs from the broker's, that NSpid shows a nested PID namespace, and that the sandboxed process disappears within 5 seconds after its intermediate parent is killed with SIGKILL.

#### Scenario: Removing PID/IPC namespaces is detected
- **WHEN** `--unshare-all` is replaced by user+net+uts unsharing (critic mutant MX6)
- **THEN** the PID-count or environ-scan probe fails

#### Scenario: Removing die-with-parent is detected
- **WHEN** `--die-with-parent` is removed (MX2)
- **THEN** the host-side parent-death check fails

#### Scenario: Removing new-session is detected
- **WHEN** `--new-session` is removed (MX4)
- **THEN** the host-side session-id check fails

#### Scenario: Capability drop mutant is classified honestly
- **WHEN** `--cap-drop ALL` is removed (MX3) under unprivileged bwrap
- **THEN** the result records `CapEff` with and without the flag, and MX3 is reported either as killed or as an equivalent mutant with that evidence, never silently as killed
