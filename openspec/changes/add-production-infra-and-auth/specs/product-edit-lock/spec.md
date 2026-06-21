## ADDED Requirements

### Requirement: Explicit pessimistic product lock
Editing a product SHALL require holding its edit lock, acquired by an explicit start-editing action (never implicitly). While another user holds a live lock, callers SHALL see the product read-only along with who holds the lock and since when. Write endpoints SHALL check role first, then lock.

#### Scenario: Second editor blocked
- **WHEN** editor B requests the lock on product P while editor A's lock is live
- **THEN** B is told A holds the lock and P stays read-only for B

#### Scenario: Write without lock
- **WHEN** an editor with sufficient role POSTs a write on P without holding P's lock
- **THEN** the request is rejected with 409

### Requirement: Heartbeat and zombie expiry
A held lock SHALL be kept alive by heartbeats (every 30s); a lock whose last heartbeat is older than 300s SHALL be treated as expired and acquirable by anyone. Steals SHALL be atomic — two concurrent claimers cannot both win.

#### Scenario: Closed tab releases within TTL
- **WHEN** the holder's browser stops heartbeating (tab closed / laptop asleep) for over 300s
- **THEN** another editor's acquire succeeds

### Requirement: Release and admin force-release
The holder SHALL be able to release their own lock; an admin SHALL be able to force-release any lock, which writes a `lock.force_release` audit entry recording the previous holder.

#### Scenario: Admin force-release
- **WHEN** an admin force-releases P's lock held by A
- **THEN** the lock is gone and the audit log records actor, P, and A

### Requirement: Background jobs run under the trigger's lock
Jobs triggered by an editor's action SHALL NOT require separate lock checks; the human-level lock guarantees one writer per product and job outputs are idempotent derived data.

#### Scenario: Job completes after lock expiry
- **WHEN** a preprocess job triggered by A finishes after A's lock expired
- **THEN** the job's writes still land (derived artifacts, idempotent)
