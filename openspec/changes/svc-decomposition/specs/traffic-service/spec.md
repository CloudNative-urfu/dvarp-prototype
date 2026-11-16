## ADDED Requirements

### Requirement: Traffic-svc contract
The legal-traffic service (implementation — issue #36) SHALL implement `TrafficPort`:
- `schedule(FlowInvite)` — the invite carries config, seed, budget (`n_legal`, `blocks`,
  `mean_gap`) and the snapshot id + hash; the answer is the sequence of blocks of legal
  arrivals; an invite whose snapshot hash differs from the service's snapshot is rejected;
- `bind(entry, entry_index, db_path, episode_id, traffic)` → `BoundEpisode`: actor, ticket,
  order, fixture rows and ground truth, bound to **real rows** of the restored snapshot
  (orders, items, tickets consistent with R1–R7 and the corpus ground truth; manifest #38);
  placeholder identifiers are forbidden; the service reads the DB, the orchestrator deploys
  the fixture;
- `client(BoundEpisode)` → text-only `UserEndpoint`; after the session `summary()` and
  `end_reason` (episode completion signal); ground truth never reaches the defender;
- `close()` — flow completion signal.
Until #36 is ready, `runner/legal_traffic.py` (current internals: `plan.build_schedule`,
`corpus.pick_accounts`/`bind`, `ResponsiveUser`) runs behind the same interface.

#### Scenario: Same snapshot for every config
- **WHEN** a run invites traffic for several configs with one seed
- **THEN** every invite carries the same snapshot id + hash and each legal episode starts
  from a restored snapshot whose hash matches it

#### Scenario: Swap to the service
- **WHEN** traffic-svc replaces `legal_traffic`
- **THEN** only the `traffic_port` factory changes and the parity test stays green
