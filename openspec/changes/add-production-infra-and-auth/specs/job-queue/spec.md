## ADDED Requirements

### Requirement: Replica-consistent job lifecycle
Jobs SHALL live in the database, not process memory: submission inserts a `queued` row and returns its id; `GET /api/jobs/{id}` SHALL answer correctly from any web replica. Submit APIs keep their current signatures (discover, preprocess, save_match, rule_check, reprocess_all).

#### Scenario: Poll lands on the other replica
- **WHEN** a job is submitted via web-1 and the status poll is routed to web-2
- **THEN** web-2 returns the job's current status

### Requirement: Exactly-once claim
Workers SHALL claim jobs with the two-step optimistic protocol (select a queued candidate, then `UPDATE … WHERE id=? AND status='queued'` judged by rowcount) so a job is never executed by two workers concurrently. The completion handling SHALL apply the same store side-effects as the current in-process done-callbacks.

#### Scenario: Two workers race
- **WHEN** two workers pick the same queued candidate
- **THEN** exactly one UPDATE wins; the loser re-selects

### Requirement: Heartbeat recovery
Running jobs SHALL heartbeat (30s); a job whose heartbeat is older than 120s SHALL be requeued if attempts < 3 and marked `error` otherwise. Workers are idempotent, so requeued jobs may safely rerun.

#### Scenario: Pod killed mid-job
- **WHEN** k8s evicts a worker while a job runs
- **THEN** within ~2 minutes the job returns to `queued` and another worker picks it up

### Requirement: Cross-replica dedupe
In-flight dedupe (one queued/running preprocess per (version, file) binding) SHALL be a database query, correct across replicas.

#### Scenario: Concurrent resubmit on two replicas
- **WHEN** the same binding is submitted on web-1 and web-2 at once
- **THEN** one submission is accepted and the other receives 409 with the in-flight job id

### Requirement: Parent/child progress and retention
`reprocess_all` SHALL track `total`/`done` on a parent row updated atomically by child completions. Jobs in `done`/`error` older than 7 days SHALL be pruned.

#### Scenario: Parent progress
- **WHEN** 3 of 10 child preprocesses have completed
- **THEN** polling the parent reports done=3, total=10
