-- Append-only local recovery authority for already-dispatched Crossref responses.
-- Owner: discovery. This never grants provider I/O or changes the historical claim.
CREATE TABLE crossref_capture_recoveries (
  claim_id TEXT PRIMARY KEY NOT NULL REFERENCES crossref_capture_claims(id),
  attempt_id TEXT NOT NULL UNIQUE REFERENCES crossref_harvest_page_attempts(id),
  receipt_id TEXT NOT NULL UNIQUE REFERENCES object_registry(object_id),
  recovered_us INTEGER NOT NULL CHECK(typeof(recovered_us)='integer'),
  reason TEXT NOT NULL CHECK(reason='durable_inbox_after_dispatch'),
  policy_version TEXT NOT NULL CHECK(policy_version='crossref-recovery-v1')
);

CREATE TRIGGER crossref_recovery_requires_exact_attempt
BEFORE INSERT ON crossref_capture_recoveries
WHEN NOT EXISTS (
  SELECT 1
  FROM crossref_capture_claims c
  JOIN crossref_harvest_page_attempts a ON a.id=NEW.attempt_id
  WHERE c.id=NEW.claim_id
    AND c.state='dispatching'
    AND a.page_id=c.page_id
    AND a.receipt_id=NEW.receipt_id
)
BEGIN SELECT RAISE(ABORT,'crossref_recovery_attempt_mismatch'); END;

CREATE TRIGGER crossref_recovery_immutable
BEFORE UPDATE ON crossref_capture_recoveries
BEGIN SELECT RAISE(ABORT,'crossref_recovery_immutable'); END;

CREATE TRIGGER crossref_recovery_keep_history
BEFORE DELETE ON crossref_capture_recoveries
BEGIN SELECT RAISE(ABORT,'crossref_recovery_history_required'); END;

CREATE TRIGGER crossref_recovery_no_replace
BEFORE INSERT ON crossref_capture_recoveries
WHEN EXISTS (
  SELECT 1 FROM crossref_capture_recoveries
  WHERE claim_id=NEW.claim_id OR attempt_id=NEW.attempt_id OR receipt_id=NEW.receipt_id
)
BEGIN SELECT RAISE(ABORT,'crossref_recovery_exists'); END;
