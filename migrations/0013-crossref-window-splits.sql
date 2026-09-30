-- Append-only topology for inclusive Crossref window splits. Owner: discovery.
-- Parent attempts and receipts remain history. Only leaves may be traversed/finalized.
CREATE TABLE crossref_window_splits (
parent_window_id TEXT PRIMARY KEY NOT NULL REFERENCES crossref_harvest_windows(id),
left_window_id TEXT UNIQUE NOT NULL REFERENCES crossref_harvest_windows(id),
right_window_id TEXT UNIQUE NOT NULL REFERENCES crossref_harvest_windows(id),
split_at TEXT NOT NULL,
reason TEXT NOT NULL CHECK(reason IN ('volume_budget','page_budget','time_budget','operator')),
created_at TEXT NOT NULL,
CHECK(parent_window_id<>left_window_id AND parent_window_id<>right_window_id),
CHECK(left_window_id<>right_window_id)
);

CREATE TRIGGER crossref_split_validate BEFORE INSERT ON crossref_window_splits BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM crossref_harvest_windows p
    JOIN crossref_harvest_windows l ON l.id=NEW.left_window_id
    JOIN crossref_harvest_windows r ON r.id=NEW.right_window_id
    WHERE p.id=NEW.parent_window_id AND p.state<>'running'
    AND l.binding_key=p.binding_key AND r.binding_key=p.binding_key
    AND l.config_version=p.config_version AND r.config_version=p.config_version
    AND l.rows=p.rows AND r.rows=p.rows
    AND l.from_index=p.from_index AND l.until_index=NEW.split_at
    AND r.from_index=NEW.split_at AND r.until_index=p.until_index
    AND julianday(p.from_index)<julianday(NEW.split_at)
    AND julianday(NEW.split_at)<julianday(p.until_index)
  ) THEN RAISE(ABORT,'crossref_split_invalid_bounds') END;
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM crossref_harvest_passes WHERE window_id=NEW.parent_window_id AND state='running'
  ) OR EXISTS (
    SELECT 1 FROM crossref_repair_runs WHERE window_id=NEW.parent_window_id AND state='running'
  ) THEN RAISE(ABORT,'crossref_split_parent_running') END;
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM crossref_window_finalizations WHERE window_id=NEW.parent_window_id
  ) THEN RAISE(ABORT,'crossref_split_parent_finalized') END;
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM crossref_window_splits WHERE left_window_id IN (NEW.left_window_id,NEW.right_window_id)
    OR right_window_id IN (NEW.left_window_id,NEW.right_window_id)
  ) THEN RAISE(ABORT,'crossref_split_child_owned') END;
END;

CREATE TRIGGER crossref_split_immutable_update BEFORE UPDATE ON crossref_window_splits BEGIN
  SELECT RAISE(ABORT,'crossref_split_immutable');
END;
CREATE TRIGGER crossref_split_immutable_delete BEFORE DELETE ON crossref_window_splits BEGIN
  SELECT RAISE(ABORT,'crossref_split_immutable');
END;
CREATE TRIGGER crossref_split_no_parent_pass BEFORE INSERT ON crossref_harvest_passes
WHEN EXISTS (SELECT 1 FROM crossref_window_splits WHERE parent_window_id=NEW.window_id) BEGIN
  SELECT RAISE(ABORT,'crossref_split_parent_superseded');
END;
CREATE TRIGGER crossref_split_no_parent_revive BEFORE UPDATE OF state ON crossref_harvest_passes
WHEN NEW.state IN ('running','completed') AND EXISTS (
  SELECT 1 FROM crossref_window_splits WHERE parent_window_id=NEW.window_id
) BEGIN
  SELECT RAISE(ABORT,'crossref_split_parent_superseded');
END;
CREATE TRIGGER crossref_split_no_parent_finalization BEFORE INSERT ON crossref_window_finalizations
WHEN EXISTS (SELECT 1 FROM crossref_window_splits WHERE parent_window_id=NEW.window_id) BEGIN
  SELECT RAISE(ABORT,'crossref_split_parent_superseded');
END;
CREATE TRIGGER crossref_split_fixed_window_identity
BEFORE UPDATE OF binding_key,query_fingerprint,config_version,from_index,until_index,rows
ON crossref_harvest_windows
WHEN EXISTS (
  SELECT 1 FROM crossref_window_splits
  WHERE OLD.id IN (parent_window_id,left_window_id,right_window_id)
) AND (NEW.binding_key<>OLD.binding_key OR NEW.query_fingerprint<>OLD.query_fingerprint
       OR NEW.config_version<>OLD.config_version OR NEW.from_index<>OLD.from_index
       OR NEW.until_index<>OLD.until_index OR NEW.rows<>OLD.rows) BEGIN
  SELECT RAISE(ABORT,'crossref_split_fixed_window_identity');
END;
