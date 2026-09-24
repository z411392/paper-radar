-- Append-only resolution evidence for dispatched Crossref capture claims.
-- Owner: discovery. Dispatching claim history remains immutable; retry creates a new claim.
CREATE TABLE crossref_capture_resolutions (
  claim_id TEXT PRIMARY KEY NOT NULL REFERENCES crossref_capture_claims(id),
  attempt_id TEXT NOT NULL UNIQUE REFERENCES crossref_harvest_page_attempts(id),
  receipt_id TEXT NOT NULL UNIQUE REFERENCES object_registry(object_id),
  action TEXT NOT NULL CHECK(action IN ('accept','retry','stop')),
  failure_code TEXT,
  resolved_us INTEGER NOT NULL CHECK(typeof(resolved_us)='integer'),
  retry_not_before_us INTEGER,
  policy_version TEXT NOT NULL,
  CHECK(
    (action='accept' AND failure_code IS NULL AND retry_not_before_us IS NULL)
    OR (action='stop' AND failure_code IS NOT NULL AND retry_not_before_us IS NULL)
    OR (action='retry' AND failure_code IS NOT NULL
        AND typeof(retry_not_before_us)='integer' AND retry_not_before_us>resolved_us)
  )
);

CREATE TRIGGER crossref_resolution_requires_exact_attempt
BEFORE INSERT ON crossref_capture_resolutions
WHEN NOT EXISTS (
  SELECT 1
  FROM crossref_capture_claims c
  JOIN crossref_harvest_page_attempts a ON a.id=NEW.attempt_id
  WHERE c.id=NEW.claim_id
    AND c.state='dispatching'
    AND a.page_id=c.page_id
    AND a.receipt_id=NEW.receipt_id
    AND a.action=NEW.action
    AND a.failure_code IS NEW.failure_code
)
BEGIN SELECT RAISE(ABORT,'crossref_resolution_attempt_mismatch'); END;

CREATE TRIGGER crossref_resolution_immutable
BEFORE UPDATE ON crossref_capture_resolutions
BEGIN SELECT RAISE(ABORT,'crossref_resolution_immutable'); END;

CREATE TRIGGER crossref_resolution_keep_history
BEFORE DELETE ON crossref_capture_resolutions
BEGIN SELECT RAISE(ABORT,'crossref_resolution_history_required'); END;

CREATE TRIGGER crossref_resolution_no_replace
BEFORE INSERT ON crossref_capture_resolutions
WHEN EXISTS (
  SELECT 1 FROM crossref_capture_resolutions
  WHERE claim_id=NEW.claim_id OR attempt_id=NEW.attempt_id OR receipt_id=NEW.receipt_id
)
BEGIN SELECT RAISE(ABORT,'crossref_resolution_exists'); END;

DROP INDEX crossref_capture_one_open_claim;
CREATE UNIQUE INDEX crossref_capture_one_reserved_claim
ON crossref_capture_claims(page_id) WHERE state='reserved';

DROP TRIGGER crossref_inbox_claim_no_replace;
CREATE TRIGGER crossref_inbox_claim_no_replace
BEFORE INSERT ON crossref_capture_claims
WHEN EXISTS (
  SELECT 1 FROM crossref_capture_claims
  WHERE id=NEW.id
     OR (page_id=NEW.page_id AND fencing_token=NEW.fencing_token)
     OR (page_id=NEW.page_id AND state='reserved')
)
BEGIN SELECT RAISE(ABORT,'crossref_capture_claim_replace_forbidden'); END;

CREATE TRIGGER crossref_capture_retry_claim_guard
BEFORE INSERT ON crossref_capture_claims
WHEN EXISTS (SELECT 1 FROM crossref_capture_claims WHERE page_id=NEW.page_id)
AND NOT (
  NEW.state='reserved'
  AND NEW.dispatched_us IS NULL
  AND NEW.ended_us IS NULL
  AND NEW.fencing_token=(
    SELECT MAX(fencing_token)+1 FROM crossref_capture_claims WHERE page_id=NEW.page_id
  )
  AND (
    EXISTS (
      SELECT 1 FROM crossref_capture_claims old
      WHERE old.page_id=NEW.page_id
        AND old.fencing_token=(
          SELECT MAX(fencing_token) FROM crossref_capture_claims WHERE page_id=NEW.page_id
        )
        AND old.state IN ('released','expired')
        AND old.dispatched_us IS NULL
    )
    OR EXISTS (
      SELECT 1
      FROM crossref_capture_claims old
      JOIN crossref_capture_resolutions r ON r.claim_id=old.id
      WHERE old.page_id=NEW.page_id
        AND old.fencing_token=(
          SELECT MAX(fencing_token) FROM crossref_capture_claims WHERE page_id=NEW.page_id
        )
        AND old.state='dispatching'
        AND r.action='retry'
        AND typeof(r.retry_not_before_us)='integer'
        AND NEW.reserved_us>=r.retry_not_before_us
        AND NEW.pass_id=old.pass_id
        AND NEW.workspace_id=old.workspace_id
        AND NEW.workspace_epoch=old.workspace_epoch
        AND NEW.request_json=old.request_json
    )
  )
)
BEGIN SELECT RAISE(ABORT,'crossref_capture_retry_not_authorized'); END;
