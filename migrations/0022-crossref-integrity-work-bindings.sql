-- Optional exact catalog bindings for Crossref integrity assertions.
-- Owner: scholarly_catalog. Missing DOI identities remain unresolved, not synthetic works.
CREATE TABLE crossref_integrity_work_bindings (
  assertion_id TEXT NOT NULL REFERENCES crossref_integrity_assertions(id),
  role TEXT NOT NULL CHECK(role IN ('notice','target')),
  canonical_doi TEXT NOT NULL,
  manifestation_id TEXT NOT NULL REFERENCES paper_manifestations(id),
  work_id TEXT NOT NULL REFERENCES paper_works(id),
  canonical_work_id TEXT NOT NULL REFERENCES paper_works(id),
  bound_at TEXT NOT NULL,
  PRIMARY KEY(assertion_id,role)
);

CREATE TRIGGER crossref_integrity_binding_immutable
BEFORE UPDATE ON crossref_integrity_work_bindings
BEGIN SELECT RAISE(ABORT,'crossref_integrity_binding_immutable'); END;

CREATE TRIGGER crossref_integrity_binding_keep_history
BEFORE DELETE ON crossref_integrity_work_bindings
BEGIN SELECT RAISE(ABORT,'crossref_integrity_binding_history_required'); END;
