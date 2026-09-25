-- Crossref post-publication integrity assertions and exact provider-revision observations.
-- Owner: scholarly_catalog. Provider assertions are append-only and do not mutate paper status.
CREATE TABLE crossref_integrity_assertions (
  id TEXT PRIMARY KEY NOT NULL,
  source_notice_doi TEXT NOT NULL
    REFERENCES crossref_source_records(canonical_doi),
  target_doi_raw TEXT,
  target_canonical_doi TEXT,
  target_normalization_state TEXT NOT NULL
    CHECK(target_normalization_state IN ('normalized','invalid','missing')),
  type_raw TEXT,
  source_raw TEXT,
  label_raw TEXT,
  record_id_raw TEXT,
  event_class TEXT NOT NULL
    CHECK(event_class IN (
      'correction','retraction','expression_of_concern',
      'reinstatement','other_update'
    )),
  updated_value TEXT,
  updated_precision TEXT
    CHECK(updated_precision IS NULL OR updated_precision IN ('year','month','day','second')),
  updated_raw_json TEXT CHECK(updated_raw_json IS NULL OR json_valid(updated_raw_json)),
  raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
  first_observed_at TEXT NOT NULL,
  CHECK(
    (target_normalization_state='normalized'
      AND target_doi_raw IS NOT NULL
      AND target_canonical_doi IS NOT NULL)
    OR
    (target_normalization_state='invalid'
      AND target_doi_raw IS NOT NULL
      AND target_canonical_doi IS NULL)
    OR
    (target_normalization_state='missing'
      AND target_doi_raw IS NULL
      AND target_canonical_doi IS NULL)
  )
);

CREATE TABLE crossref_integrity_revision_observations (
  provider_revision_id TEXT NOT NULL
    REFERENCES crossref_provider_revisions(id),
  update_ordinal INTEGER NOT NULL CHECK(typeof(update_ordinal)='integer' AND update_ordinal>=0),
  assertion_id TEXT NOT NULL REFERENCES crossref_integrity_assertions(id),
  observed_at TEXT NOT NULL,
  PRIMARY KEY(provider_revision_id,update_ordinal)
);

CREATE TABLE crossref_integrity_parse_gaps (
  id TEXT PRIMARY KEY NOT NULL,
  provider_revision_id TEXT NOT NULL
    REFERENCES crossref_provider_revisions(id),
  path TEXT NOT NULL,
  error_code TEXT NOT NULL,
  raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
  observed_at TEXT NOT NULL,
  UNIQUE(provider_revision_id,path,error_code)
);

CREATE TRIGGER crossref_integrity_assertion_immutable
BEFORE UPDATE ON crossref_integrity_assertions
BEGIN SELECT RAISE(ABORT,'crossref_integrity_assertion_immutable'); END;
CREATE TRIGGER crossref_integrity_assertion_keep_history
BEFORE DELETE ON crossref_integrity_assertions
BEGIN SELECT RAISE(ABORT,'crossref_integrity_assertion_history_required'); END;

CREATE TRIGGER crossref_integrity_observation_immutable
BEFORE UPDATE ON crossref_integrity_revision_observations
BEGIN SELECT RAISE(ABORT,'crossref_integrity_observation_immutable'); END;
CREATE TRIGGER crossref_integrity_observation_keep_history
BEFORE DELETE ON crossref_integrity_revision_observations
BEGIN SELECT RAISE(ABORT,'crossref_integrity_observation_history_required'); END;

CREATE TRIGGER crossref_integrity_gap_immutable
BEFORE UPDATE ON crossref_integrity_parse_gaps
BEGIN SELECT RAISE(ABORT,'crossref_integrity_gap_immutable'); END;
CREATE TRIGGER crossref_integrity_gap_keep_history
BEFORE DELETE ON crossref_integrity_parse_gaps
BEGIN SELECT RAISE(ABORT,'crossref_integrity_gap_history_required'); END;
