-- Append-only pre-send claims. Owner: discovery. This does not index response objects.
-- An unresolved dispatch is never made retryable solely by lease expiry.
CREATE TABLE crossref_capture_claims (
  id TEXT PRIMARY KEY NOT NULL,
  page_id TEXT NOT NULL REFERENCES crossref_harvest_pages(id),
  pass_id TEXT NOT NULL REFERENCES crossref_harvest_passes(id),
  fencing_token INTEGER NOT NULL CHECK(typeof(fencing_token)='integer' AND fencing_token>0),
  owner_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  workspace_epoch INTEGER NOT NULL CHECK(typeof(workspace_epoch)='integer' AND workspace_epoch>0),
  request_json TEXT NOT NULL CHECK(json_valid(request_json)),
  reserved_us INTEGER NOT NULL CHECK(typeof(reserved_us)='integer'),
  lease_until_us INTEGER NOT NULL CHECK(typeof(lease_until_us)='integer' AND lease_until_us>reserved_us),
  state TEXT NOT NULL CHECK(state IN ('reserved','dispatching','released','expired')),
  dispatched_us INTEGER,
  ended_us INTEGER,
  UNIQUE(page_id,fencing_token),
  CHECK(
    (state='reserved' AND dispatched_us IS NULL AND ended_us IS NULL)
    OR (state='dispatching' AND typeof(dispatched_us)='integer'
        AND dispatched_us>=reserved_us AND dispatched_us<lease_until_us AND ended_us IS NULL)
    OR (state IN ('released','expired') AND dispatched_us IS NULL
        AND typeof(ended_us)='integer' AND ended_us>=reserved_us)
  )
);
CREATE UNIQUE INDEX crossref_capture_one_open_claim ON crossref_capture_claims(page_id)
  WHERE state IN ('reserved','dispatching');
CREATE TRIGGER crossref_capture_claim_page_identity BEFORE INSERT ON crossref_capture_claims
WHEN NOT EXISTS (
  SELECT 1 FROM crossref_harvest_pages WHERE id=NEW.page_id AND pass_id=NEW.pass_id
) BEGIN
  SELECT RAISE(ABORT,'crossref_claim_page_identity');
END;
CREATE TRIGGER crossref_capture_claim_identity_immutable
BEFORE UPDATE OF id,page_id,pass_id,fencing_token,owner_id,workspace_id,workspace_epoch,
  request_json,reserved_us,lease_until_us ON crossref_capture_claims BEGIN
  SELECT RAISE(ABORT,'crossref_claim_identity_immutable');
END;
CREATE TRIGGER crossref_capture_claim_transition BEFORE UPDATE ON crossref_capture_claims
WHEN NOT (OLD.state='reserved' AND NEW.state IN ('dispatching','released','expired')) BEGIN
  SELECT RAISE(ABORT,'crossref_claim_transition_invalid');
END;
CREATE TRIGGER crossref_capture_claim_keep_history BEFORE DELETE ON crossref_capture_claims BEGIN
  SELECT RAISE(ABORT,'crossref_claim_history_required');
END;
