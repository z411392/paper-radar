-- Durable, indexed response staging before filesystem object publication. Owner: discovery.
-- No claim is released and no page advances here. An absent row is an unknown outcome.
CREATE TABLE crossref_capture_inbox (
  claim_id TEXT PRIMARY KEY NOT NULL REFERENCES crossref_capture_claims(id),
  envelope BLOB NOT NULL CHECK(typeof(envelope)='blob' AND length(envelope) BETWEEN 1 AND 2000000),
  envelope_sha256 TEXT NOT NULL CHECK(length(envelope_sha256)=64
    AND envelope_sha256 NOT GLOB '*[^0-9a-f]*'),
  body BLOB NOT NULL CHECK(typeof(body)='blob' AND length(body)<=8000000),
  body_sha256 TEXT NOT NULL CHECK(length(body_sha256)=64 AND body_sha256 NOT GLOB '*[^0-9a-f]*'),
  staged_us INTEGER NOT NULL CHECK(typeof(staged_us)='integer')
);
CREATE TRIGGER crossref_inbox_requires_dispatch BEFORE INSERT ON crossref_capture_inbox
WHEN NOT EXISTS (SELECT 1 FROM crossref_capture_claims WHERE id=NEW.claim_id AND state='dispatching')
BEGIN SELECT RAISE(ABORT,'crossref_inbox_requires_dispatch'); END;
CREATE TRIGGER crossref_inbox_immutable BEFORE UPDATE ON crossref_capture_inbox
BEGIN SELECT RAISE(ABORT,'crossref_inbox_immutable'); END;
CREATE TRIGGER crossref_inbox_keep_history BEFORE DELETE ON crossref_capture_inbox
BEGIN SELECT RAISE(ABORT,'crossref_inbox_history_required'); END;

-- REPLACE can bypass DELETE triggers with recursive_triggers=OFF: guard before insertion.
CREATE TRIGGER crossref_inbox_single_response BEFORE INSERT ON crossref_capture_inbox
WHEN EXISTS (SELECT 1 FROM crossref_capture_inbox WHERE claim_id=NEW.claim_id)
BEGIN SELECT RAISE(ABORT,'crossref_inbox_response_exists'); END;
CREATE TRIGGER crossref_inbox_claim_no_replace BEFORE INSERT ON crossref_capture_claims
WHEN EXISTS (SELECT 1 FROM crossref_capture_claims WHERE id=NEW.id
  OR (page_id=NEW.page_id AND (fencing_token=NEW.fencing_token OR state IN ('reserved','dispatching'))))
BEGIN SELECT RAISE(ABORT,'crossref_capture_claim_replace_forbidden'); END;
