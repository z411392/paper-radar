-- Append-only audit for replacing a cancelled, never-dispatched digest slot.
-- Owner: delivery. The prior rendered artifact remains immutable in object_registry.

CREATE TABLE delivery_digest_rebuilds (
id TEXT NOT NULL PRIMARY KEY,
digest_id TEXT NOT NULL REFERENCES digests(id),
outbox_id TEXT NOT NULL REFERENCES delivery_outbox(id),
generation INTEGER NOT NULL CHECK(generation>0),
prior_rendered_object_id TEXT NOT NULL REFERENCES object_registry(object_id),
prior_payload_sha256 TEXT NOT NULL,
prior_idempotency_key TEXT NOT NULL,
prior_workspace_epoch INTEGER NOT NULL CHECK(prior_workspace_epoch>0),
prior_cutoff_at TEXT NOT NULL,
reason TEXT NOT NULL,
recorded_at TEXT NOT NULL,
UNIQUE(digest_id,generation)
);

CREATE TRIGGER delivery_digest_rebuild_immutable
BEFORE UPDATE ON delivery_digest_rebuilds
BEGIN SELECT RAISE(ABORT,'delivery_digest_rebuild_immutable'); END;

CREATE TRIGGER delivery_digest_rebuild_history_required
BEFORE DELETE ON delivery_digest_rebuilds
BEGIN SELECT RAISE(ABORT,'delivery_digest_rebuild_history_required'); END;
