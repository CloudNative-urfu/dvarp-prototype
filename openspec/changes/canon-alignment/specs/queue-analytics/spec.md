## ADDED Requirements

### Requirement: Escalation share by traffic class
Metrics SHALL report the escalation share `E(g)` measured in paired runs and split by traffic
class: `E_L` (legal episodes; natural and false-positive parts) and `E_A` (attack episodes),
plus the legal escalation share of the base flow of the pair. Escalations are reported as a
neutral outcome, not as violations.

#### Scenario: Split is consistent
- **WHEN** metrics are computed for any configuration
- **THEN** `E_L_natural + E_L_fp = E_L` and `E_L`, `E_A`, `E_L_base_flow` are present

### Requirement: Analytic queue block
The legal-flow delay SHALL be computed analytically, not simulated: `λ_Q = λ_L·E_L + λ_A·E_A`
at a fixed intensity from the run plan; M/M/1 at one operator, M/M/c (Erlang C) at K > 1;
conditional wait `W_q`; unconditional delay per legal client `Q_L = E_L·W_q`; SLA probability
`P(W_q > Q_SLA)` and per legal client `P_sla_L = E_L·P(W_q > Q_SLA)`; a sensitivity grid of
service time 4/8/15 min × 1/2 operators. Customer abandonment MUST NOT be modelled (non-claim).
The formulas MUST pass an assert-based self-check (M/M/c with c = 1 equals M/M/1; textbook
Erlang C values).

#### Scenario: Report shows the block
- **WHEN** the offline report is built
- **THEN** it contains, per configuration, `E_L` (natural / FP), `E_A`, `λ_Q`, the queue model,
  `ρ`, `W_q`, `Q_L`, `P(W_q > Q_SLA)`, `P_sla_L` and the K×S grid

#### Scenario: Unstable queue
- **WHEN** `ρ ≥ 1` in the base cell of a configuration or of the reference
- **THEN** its break-even status is `queue_unstable`, L* is undefined and the configuration is
  not on the Pareto front

### Requirement: Analytic queue cost in L*
The break-even numerator SHALL use `C_Q = C_queue·ΔQ_L + C_sla·ΔP_sla_L` weighted by `w_L`, with
ΔQ the analytic estimate; `P_abandon` MUST NOT appear. The discrete-event queue simulation MUST
NOT be in the measurement path; it MAY remain as a numerical check of the analytic formula.

#### Scenario: ΔQ is analytic
- **WHEN** L* of a stable configuration is computed
- **THEN** its `dQ` term equals `Q_L(g) − Q_L(g0)` from the analytic block
