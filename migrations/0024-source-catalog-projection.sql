-- Durable orchestration progress for projecting closed source units into the catalog.
-- Owner: research_workflow. Source observations remain discovery-owned.
CREATE TABLE source_catalog_projection_progress (
  unit_id TEXT NOT NULL REFERENCES harvest_units(id),
  source TEXT NOT NULL CHECK(source IN ('arxiv','pubmed')),
  last_observation_id TEXT REFERENCES source_observations(id),
  projected_count INTEGER NOT NULL DEFAULT 0
    CHECK(typeof(projected_count)='integer' AND projected_count>=0),
  state TEXT NOT NULL DEFAULT 'running' CHECK(state IN ('running','succeeded')),
  checkpoint_version INTEGER NOT NULL DEFAULT 0
    CHECK(typeof(checkpoint_version)='integer' AND checkpoint_version>=0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(unit_id,source),
  CHECK(
    (projected_count=0 AND last_observation_id IS NULL)
    OR (projected_count>0 AND last_observation_id IS NOT NULL)
  )
);

CREATE TRIGGER source_catalog_projection_unit_source
BEFORE INSERT ON source_catalog_projection_progress
WHEN NOT EXISTS (
  SELECT 1
  FROM harvest_units u
  JOIN source_bindings b ON b.id=u.binding_id
  WHERE u.id=NEW.unit_id AND b.source=NEW.source
)
BEGIN SELECT RAISE(ABORT,'source_catalog_projection_unit_source'); END;

CREATE TRIGGER source_catalog_projection_cursor_identity_insert
BEFORE INSERT ON source_catalog_projection_progress
WHEN NEW.last_observation_id IS NOT NULL
AND NOT EXISTS (
  SELECT 1 FROM source_observations o
  WHERE o.id=NEW.last_observation_id
    AND o.unit_id=NEW.unit_id
    AND o.source=NEW.source
)
BEGIN SELECT RAISE(ABORT,'source_catalog_projection_cursor_identity'); END;

CREATE TRIGGER source_catalog_projection_cursor_identity_update
BEFORE UPDATE OF last_observation_id ON source_catalog_projection_progress
WHEN NEW.last_observation_id IS NOT NULL
AND NOT EXISTS (
  SELECT 1 FROM source_observations o
  WHERE o.id=NEW.last_observation_id
    AND o.unit_id=NEW.unit_id
    AND o.source=NEW.source
)
BEGIN SELECT RAISE(ABORT,'source_catalog_projection_cursor_identity'); END;

CREATE TRIGGER source_catalog_projection_identity_immutable
BEFORE UPDATE OF unit_id,source,created_at ON source_catalog_projection_progress
BEGIN SELECT RAISE(ABORT,'source_catalog_projection_identity_immutable'); END;

CREATE TRIGGER source_catalog_projection_no_delete
BEFORE DELETE ON source_catalog_projection_progress
BEGIN SELECT RAISE(ABORT,'source_catalog_projection_history_required'); END;
