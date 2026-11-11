## ADDED Requirements

### Requirement: Primary L* table uses the v2r foundation
The primary end-to-end run of verify and its L*, Pareto and dominance report SHALL use `--foundation v2r` with seeds 7, 8, 9, and the report MUST state the foundation. The v1 run MUST be kept as a separate step labelled legacy.

#### Scenario: Foundation stated
- **WHEN** verify finishes
- **THEN** the primary `metrics/report.md` states `foundation=v2r` and the legacy v1 report states `foundation=v1 (legacy)`

#### Scenario: Primary run on v1 is detected
- **WHEN** a mutant runs the primary step with `--foundation v1`
- **THEN** the `primary_foundation` verify step fails

#### Scenario: Legacy table regression
- **WHEN** the legacy v1 step runs on seeds used before this change
- **THEN** its tables are byte-identical to the previous primary v1 tables

### Requirement: Acceptance does not depend on effect sign
Acceptance of FR-1.23 and FR-1.24 on the v2r table SHALL check that statuses follow the break-even rules (V(g0)−V(g)=0 → no_safety_gain; <0 → more_violations; CI containing 0 → unresolved) and MUST NOT require any configuration to have a positive safety gain.

#### Scenario: All configurations without gain
- **WHEN** every configuration on v2r has V(g0)−V(g)=0
- **THEN** acceptance passes and the report lists them as no_safety_gain

#### Scenario: Wrong status is caught
- **WHEN** a mutant labels a configuration with V(g0)−V(g)=0 as pays_at_threshold
- **THEN** acceptance fails
