import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone

from libs.scholarly_catalog.dtos.normalized_identifier import NormalizedIdentifier
from libs.scholarly_catalog.dtos.paper_identity_observation import PaperIdentityObservation
from libs.scholarly_catalog.dtos.paper_identity_resolution import PaperIdentityResolution
from libs.scholarly_catalog.dtos.paper_identity_view import (
    PaperIdentityView,
    PaperRevisionView,
)
from libs.scholarly_catalog.dtos.work_alias_result import WorkAliasResult
from libs.scholarly_catalog.dtos.work_relation_result import WorkRelationResult
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


class SqlitePaperIdentityStoreAdapter:
    _MANIFESTATION_KINDS = {"preprint", "publication", "notice", "repository_copy"}
    _PUBLICATION_STATUSES = {
        "preprint",
        "published",
        "corrected",
        "retracted",
        "withdrawn",
        "unknown",
    }

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _hash(*parts: str) -> str:
        digest = hashlib.sha256()
        for part in parts:
            digest.update(part.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _time(value: datetime | None, *, required: bool = False) -> str | None:
        if value is None:
            if required:
                raise PaperIdentityError("invalid_observation")
            return None
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PaperIdentityError("invalid_observation")
        try:
            return value.astimezone(timezone.utc).isoformat()
        except (OverflowError, ValueError) as exc:
            raise PaperIdentityError("invalid_observation") from exc

    @staticmethod
    def _text(value: object, *, maximum_bytes: int) -> str:
        if not isinstance(value, str):
            raise PaperIdentityError("invalid_observation")
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise PaperIdentityError("invalid_observation") from exc
        if (
            not value
            or value != value.strip()
            or size > maximum_bytes
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise PaperIdentityError("invalid_observation")
        return value

    @classmethod
    def _validate(
        cls,
        observation: PaperIdentityObservation,
        identifier: NormalizedIdentifier,
    ) -> tuple[str, str | None, str | None, str]:
        if not isinstance(observation, PaperIdentityObservation) or not isinstance(
            identifier, NormalizedIdentifier
        ):
            raise PaperIdentityError("invalid_observation")
        if observation.identifier_namespace != identifier.namespace:
            raise PaperIdentityError("identifier_mismatch")
        cls._text(observation.source_observation_id, maximum_bytes=256)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,127}", observation.source_observation_id) is None:
            raise PaperIdentityError("invalid_observation")
        cls._text(observation.title, maximum_bytes=8192)
        cls._text(observation.landing_url, maximum_bytes=8192)
        if (
            not isinstance(observation.content_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", observation.content_fingerprint) is None
            or observation.manifestation_kind not in cls._MANIFESTATION_KINDS
            or observation.publication_status not in cls._PUBLICATION_STATUSES
        ):
            raise PaperIdentityError("invalid_observation")
        observed_at = cls._time(observation.observed_at, required=True)
        source_updated_at = cls._time(observation.source_updated_at)
        published_at = cls._time(observation.published_at)
        assert observed_at is not None
        if source_updated_at is not None and source_updated_at > observed_at:
            raise PaperIdentityError("invalid_observation")
        return observed_at, source_updated_at, published_at, observation.content_fingerprint

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            if connection.in_transaction:
                raise PaperIdentityError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise PaperIdentityError("foreign_keys_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with connection:
                yield connection
        except sqlite3.IntegrityError as exc:
            raise PaperIdentityError("identity_database_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "identity_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "identity_database_error"
            )
            raise PaperIdentityError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def _provenance_payload(
        cls,
        observation: PaperIdentityObservation,
        identifier: NormalizedIdentifier,
        observed_at: str,
        source_updated_at: str | None,
        published_at: str | None,
    ) -> str:
        return cls._json(
            {
                "format_version": 1,
                "source_observation_id": observation.source_observation_id,
                "identifier_namespace": identifier.namespace,
                "normalized_identifier": identifier.normalized_value,
                "native_version": identifier.native_version,
                "title": observation.title,
                "content_fingerprint": observation.content_fingerprint,
                "manifestation_kind": observation.manifestation_kind,
                "landing_url": observation.landing_url,
                "publication_status": observation.publication_status,
                "observed_at": observed_at,
                "source_updated_at": source_updated_at,
                "published_at": published_at,
            }
        )

    @staticmethod
    def _manifestation(
        connection: sqlite3.Connection,
        identifier: NormalizedIdentifier,
    ) -> sqlite3.Row | None:
        row = connection.execute(
            "SELECT manifestation_id FROM external_identifiers "
            "WHERE namespace=? AND normalized_value=?",
            (identifier.namespace, identifier.normalized_value),
        ).fetchone()
        if row is None:
            return None
        manifestation = connection.execute(
            "SELECT * FROM paper_manifestations WHERE id=?",
            (row["manifestation_id"],),
        ).fetchone()
        if manifestation is None:
            raise PaperIdentityError("identity_corrupt")
        return manifestation

    @staticmethod
    def _revision(
        connection: sqlite3.Connection,
        manifestation_id: str,
        content_fingerprint: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM paper_revisions WHERE manifestation_id=? AND content_fingerprint=?",
            (manifestation_id, content_fingerprint),
        ).fetchone()

    def register(
        self,
        observation: PaperIdentityObservation,
        identifier: NormalizedIdentifier,
    ) -> PaperIdentityResolution:
        observed_at, source_updated_at, published_at, fingerprint = self._validate(
            observation, identifier
        )
        provenance_json = self._provenance_payload(
            observation,
            identifier,
            observed_at,
            source_updated_at,
            published_at,
        )
        provenance_id = "prov:" + self._hash("source-observation-v1", observation.source_observation_id)
        created_work = created_manifestation = created_revision = False

        with self._transaction(write=True) as connection:
            existing_provenance = connection.execute(
                "SELECT work_id,value_json,source_observation_id FROM catalog_field_provenance WHERE id=?",
                (provenance_id,),
            ).fetchone()
            if existing_provenance is not None and (
                existing_provenance["source_observation_id"] != observation.source_observation_id
                or existing_provenance["value_json"] != provenance_json
            ):
                raise PaperIdentityError("source_observation_conflict")

            manifestation = self._manifestation(connection, identifier)
            if manifestation is None:
                identity_key = f"{identifier.namespace}:{identifier.normalized_value}"
                work_id = "work:" + self._hash("work-identity-v1", identity_key)
                manifestation_id = "manifestation:" + self._hash(
                    "manifestation-identity-v1", identity_key
                )
                existing_work = connection.execute(
                    "SELECT * FROM paper_works WHERE id=?", (work_id,)
                ).fetchone()
                if existing_work is not None:
                    raise PaperIdentityError("identity_collision")
                first_public_date = published_at
                connection.execute(
                    "INSERT INTO paper_works("
                    "id,canonical_title,publication_status,first_public_date,"
                    "first_public_precision,first_seen_at,created_at"
                    ") VALUES(?,?,?,?,?,?,?)",
                    (
                        work_id,
                        observation.title,
                        observation.publication_status,
                        first_public_date,
                        "second" if first_public_date is not None else None,
                        observed_at,
                        observed_at,
                    ),
                )
                connection.execute(
                    "INSERT INTO paper_manifestations("
                    "id,work_id,source_namespace,native_id,manifestation_kind,landing_url,created_at"
                    ") VALUES(?,?,?,?,?,?,?)",
                    (
                        manifestation_id,
                        work_id,
                        identifier.namespace,
                        identifier.normalized_value,
                        observation.manifestation_kind,
                        observation.landing_url,
                        observed_at,
                    ),
                )
                connection.execute(
                    "INSERT INTO external_identifiers("
                    "namespace,normalized_value,manifestation_id,source_evidence_id"
                    ") VALUES(?,?,?,?)",
                    (
                        identifier.namespace,
                        identifier.normalized_value,
                        manifestation_id,
                        observation.source_observation_id,
                    ),
                )
                manifestation = connection.execute(
                    "SELECT * FROM paper_manifestations WHERE id=?", (manifestation_id,)
                ).fetchone()
                assert manifestation is not None
                created_work = created_manifestation = True
            else:
                work_id = manifestation["work_id"]
                manifestation_id = manifestation["id"]
                if (
                    manifestation["source_namespace"] != identifier.namespace
                    or manifestation["native_id"] != identifier.normalized_value
                    or manifestation["manifestation_kind"] != observation.manifestation_kind
                ):
                    raise PaperIdentityError("manifestation_conflict")

            revision = self._revision(connection, manifestation_id, fingerprint)
            if revision is None:
                revision_id = "revision:" + self._hash(
                    "revision-identity-v1", manifestation_id, fingerprint
                )
                collision = connection.execute(
                    "SELECT 1 FROM paper_revisions WHERE id=?", (revision_id,)
                ).fetchone()
                if collision is not None:
                    raise PaperIdentityError("identity_collision")
                connection.execute(
                    "INSERT INTO paper_revisions("
                    "id,manifestation_id,work_id,native_version,content_fingerprint,title,"
                    "abstract_object_id,source_updated_at,published_date,date_precision,observed_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        revision_id,
                        manifestation_id,
                        work_id,
                        identifier.native_version,
                        fingerprint,
                        observation.title,
                        None,
                        source_updated_at,
                        published_at,
                        "second" if published_at is not None else None,
                        observed_at,
                    ),
                )
                created_revision = True
            else:
                revision_id = revision["id"]
                if (
                    revision["work_id"] != work_id
                    or revision["native_version"] != identifier.native_version
                    or revision["title"] != observation.title
                    or revision["source_updated_at"] != source_updated_at
                    or revision["published_date"] != published_at
                ):
                    raise PaperIdentityError("revision_conflict")

            if existing_provenance is None:
                connection.execute(
                    "INSERT INTO catalog_field_provenance("
                    "id,work_id,field_name,value_json,source_observation_id,"
                    "preference_reason,observed_at"
                    ") VALUES(?,?,?,?,?,?,?)",
                    (
                        provenance_id,
                        work_id,
                        "source_observation",
                        provenance_json,
                        observation.source_observation_id,
                        None,
                        observed_at,
                    ),
                )
            elif existing_provenance["work_id"] != work_id:
                raise PaperIdentityError("source_observation_conflict")

            return PaperIdentityResolution(
                work_id,
                work_id,
                manifestation_id,
                revision_id,
                identifier.namespace,
                identifier.normalized_value,
                identifier.native_version,
                created_work,
                created_manifestation,
                created_revision,
            )

    def read(self, identifier: NormalizedIdentifier) -> PaperIdentityView:
        if not isinstance(identifier, NormalizedIdentifier):
            raise PaperIdentityError("invalid_identifier")
        with self._transaction(write=False) as connection:
            manifestation = self._manifestation(connection, identifier)
            if manifestation is None:
                raise PaperIdentityError("identity_missing")
            work_id = manifestation["work_id"]
            rows = connection.execute(
                "SELECT id,native_version,content_fingerprint,title,observed_at "
                "FROM paper_revisions WHERE manifestation_id=? "
                "ORDER BY observed_at,id",
                (manifestation["id"],),
            ).fetchall()
            if not rows:
                raise PaperIdentityError("identity_corrupt")
            revisions = tuple(
                PaperRevisionView(
                    row["id"],
                    row["native_version"],
                    row["content_fingerprint"],
                    row["title"],
                    row["observed_at"],
                )
                for row in rows
            )
            return PaperIdentityView(
                work_id,
                work_id,
                manifestation["id"],
                identifier.namespace,
                identifier.normalized_value,
                revisions,
            )

    def merge_alias(
        self,
        alias_work_id: str,
        canonical_work_id: str,
        evidence_json: str,
        decided_at: object,
    ) -> WorkAliasResult:
        raise PaperIdentityError("not_implemented")

    def revoke_alias(
        self,
        alias_work_id: str,
        evidence_json: str,
        revoked_at: object,
    ) -> WorkRelationResult:
        raise PaperIdentityError("not_implemented")

    def record_relation(
        self,
        source_work_id: str,
        target_work_id: str,
        relation_type: str,
        evidence_json: str,
        observed_at: object,
    ) -> WorkRelationResult:
        raise PaperIdentityError("not_implemented")
