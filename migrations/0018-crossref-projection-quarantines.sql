-- Durable post-decode projection quarantine for Crossref items.
-- Owner: discovery. This does not mutate 0011 item rows or erase provider evidence.
CREATE TABLE crossref_projection_quarantines (
  page_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK(typeof(ordinal)='integer' AND ordinal>=0),
  error_code TEXT NOT NULL,
  canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256)=64
    AND canonical_sha256 NOT GLOB '*[^0-9a-f]*'),
  quarantined_at TEXT NOT NULL,
  PRIMARY KEY(page_id,ordinal),
  FOREIGN KEY(page_id,ordinal)
    REFERENCES crossref_harvest_items(page_id,ordinal)
);

CREATE TRIGGER crossref_projection_quarantine_immutable
BEFORE UPDATE ON crossref_projection_quarantines
BEGIN SELECT RAISE(ABORT,'crossref_projection_quarantine_immutable'); END;

CREATE TRIGGER crossref_projection_quarantine_keep_history
BEFORE DELETE ON crossref_projection_quarantines
BEGIN SELECT RAISE(ABORT,'crossref_projection_quarantine_history_required'); END;
