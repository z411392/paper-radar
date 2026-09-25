-- Crossref typed relation assertions and exact provider-revision observations.
-- Owner: scholarly_catalog. REST-visible reciprocal edges are preserved, never synthesized.
CREATE TABLE crossref_relation_assertions (
  id TEXT PRIMARY KEY NOT NULL,
  source_canonical_doi TEXT NOT NULL
    REFERENCES crossref_source_records(canonical_doi),
  predicate_raw TEXT NOT NULL,
  target_id_type_raw TEXT NOT NULL,
  target_value_raw TEXT NOT NULL,
  asserted_by_raw TEXT,
  relation_class TEXT NOT NULL
    CHECK(relation_class IN ('intra_work','inter_work','unknown')),
  target_namespace TEXT,
  target_normalized_value TEXT,
  target_normalization_state TEXT NOT NULL
    CHECK(target_normalization_state IN ('normalized','raw','invalid')),
  first_observed_at TEXT NOT NULL,
  CHECK(
    (target_normalization_state='normalized'
      AND target_namespace IS NOT NULL
      AND target_normalized_value IS NOT NULL)
    OR
    (target_normalization_state IN ('raw','invalid')
      AND target_namespace IS NULL
      AND target_normalized_value IS NULL)
  )
);

CREATE TABLE crossref_relation_revision_observations (
  provider_revision_id TEXT NOT NULL
    REFERENCES crossref_provider_revisions(id),
  ordinal INTEGER NOT NULL CHECK(typeof(ordinal)='integer' AND ordinal>=0),
  assertion_id TEXT NOT NULL REFERENCES crossref_relation_assertions(id),
  observed_at TEXT NOT NULL,
  PRIMARY KEY(provider_revision_id,ordinal)
);

CREATE TABLE crossref_relation_parse_gaps (
  id TEXT PRIMARY KEY NOT NULL,
  provider_revision_id TEXT NOT NULL
    REFERENCES crossref_provider_revisions(id),
  path TEXT NOT NULL,
  error_code TEXT NOT NULL,
  raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
  observed_at TEXT NOT NULL,
  UNIQUE(provider_revision_id,path,error_code)
);

CREATE TRIGGER crossref_relation_assertion_immutable
BEFORE UPDATE ON crossref_relation_assertions
BEGIN SELECT RAISE(ABORT,'crossref_relation_assertion_immutable'); END;
CREATE TRIGGER crossref_relation_assertion_keep_history
BEFORE DELETE ON crossref_relation_assertions
BEGIN SELECT RAISE(ABORT,'crossref_relation_assertion_history_required'); END;

CREATE TRIGGER crossref_relation_observation_immutable
BEFORE UPDATE ON crossref_relation_revision_observations
BEGIN SELECT RAISE(ABORT,'crossref_relation_observation_immutable'); END;
CREATE TRIGGER crossref_relation_observation_keep_history
BEFORE DELETE ON crossref_relation_revision_observations
BEGIN SELECT RAISE(ABORT,'crossref_relation_observation_history_required'); END;

CREATE TRIGGER crossref_relation_gap_immutable
BEFORE UPDATE ON crossref_relation_parse_gaps
BEGIN SELECT RAISE(ABORT,'crossref_relation_gap_immutable'); END;
CREATE TRIGGER crossref_relation_gap_keep_history
BEFORE DELETE ON crossref_relation_parse_gaps
BEGIN SELECT RAISE(ABORT,'crossref_relation_gap_history_required'); END;
