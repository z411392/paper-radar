import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.watch_profiles.dtos.configuration_document import ConfigurationDocument
from libs.watch_profiles.dtos.domain_definition import DomainDefinition
from libs.watch_profiles.dtos.domain_seed_outcome import DomainSeedOutcome
from libs.watch_profiles.dtos.profile_revision import ProfileRevision
from libs.watch_profiles.exceptions.watch_configuration_error import WatchConfigurationError


class SqliteWatchProfileStoreAdapter:
    """Own only watch-profile tables; inject the configured connection factory at composition."""

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            raise WatchConfigurationError("storage_error", str(exc)) from exc
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _revision(value: int | None) -> None:
        if value is not None and (type(value) is not int or not 1 <= value < 2**63):
            raise WatchConfigurationError("invalid_revision")

    def import_domains(self, document: ConfigurationDocument) -> tuple[DomainSeedOutcome, ...]:
        if document.kind != "domains":
            raise WatchConfigurationError("invalid_document_kind")
        items = json.loads(document.canonical_json)["domains"]
        results = []
        with self._transaction(write=True) as connection:
            for item in items:
                encoded = self._json(item)
                row = connection.execute(
                    "SELECT revision,definition_json FROM domain_definitions WHERE id=? "
                    "ORDER BY revision DESC LIMIT 1",
                    (item["id"],),
                ).fetchone()
                if row is not None:
                    disposition = "unchanged" if row[1] == encoded else "preserved"
                    results.append(DomainSeedOutcome(item["id"], row[0], disposition))
                    continue
                connection.execute(
                    "INSERT INTO domain_definitions(id,name,definition_json,revision,updated_at) "
                    "VALUES(?,?,?,1,?)",
                    (item["id"], item["name"], encoded, datetime.now(timezone.utc).isoformat()),
                )
                results.append(DomainSeedOutcome(item["id"], 1, "created"))
        return tuple(results)

    @staticmethod
    def _domain_identifier(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None:
            raise WatchConfigurationError("invalid_domain_definition")
        return value

    @staticmethod
    def _domain_text(value: object) -> str:
        if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
            raise WatchConfigurationError("invalid_domain_definition")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise WatchConfigurationError("invalid_domain_definition") from exc
        return value

    @classmethod
    def _domain_strings(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise WatchConfigurationError("invalid_domain_definition")
        items = tuple(cls._domain_text(item) for item in value)
        if len(items) != len(set(items)) or tuple(sorted(items)) != items:
            raise WatchConfigurationError("invalid_domain_definition")
        return items

    @staticmethod
    def _unique_domain_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise WatchConfigurationError("invalid_domain_definition")
            result[key] = value
        return result

    def read_domain(self, domain_id: str, revision: int) -> DomainDefinition:
        self._revision(revision)
        domain_id = self._domain_identifier(domain_id)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT name,definition_json FROM domain_definitions WHERE id=? AND revision=?",
                (domain_id, revision),
            ).fetchone()
            if row is None:
                raise WatchConfigurationError("domain_revision_missing", domain_id)
            try:
                raw = json.loads(row[1], object_pairs_hook=self._unique_domain_keys)
                expected = {
                    "id",
                    "name",
                    "aliases",
                    "include",
                    "exclude",
                    "sources",
                    "source_categories",
                }
                if not isinstance(raw, dict) or set(raw) != expected:
                    raise WatchConfigurationError("invalid_domain_definition")
                if raw["id"] != domain_id or self._domain_text(raw["name"]) != row[0]:
                    raise WatchConfigurationError("invalid_domain_definition")
                sources = self._domain_strings(raw["sources"])
                for source in sources:
                    self._domain_identifier(source)
                categories = raw["source_categories"]
                if not isinstance(categories, dict) or not set(categories) <= set(sources):
                    raise WatchConfigurationError("invalid_domain_definition")
                normalized_categories = tuple(
                    (self._domain_identifier(source), self._domain_strings(values))
                    for source, values in sorted(categories.items())
                )
                canonical = self._json(raw)
                if canonical != row[1]:
                    raise WatchConfigurationError("invalid_domain_definition")
                return DomainDefinition(
                    domain_id,
                    revision,
                    row[0],
                    self._domain_strings(raw["aliases"]),
                    self._domain_strings(raw["include"]),
                    self._domain_strings(raw["exclude"]),
                    sources,
                    normalized_categories,
                )
            except (json.JSONDecodeError, TypeError, KeyError, RecursionError, UnicodeError):
                raise WatchConfigurationError("invalid_domain_definition") from None

    def publish(self, document: ConfigurationDocument, expected_revision: int | None) -> ProfileRevision:
        if document.kind != "profile":
            raise WatchConfigurationError("invalid_document_kind")
        self._revision(expected_revision)
        data = json.loads(document.canonical_json)
        profile_id = data["id"]
        with self._transaction(write=True) as connection:
            existing = connection.execute(
                "SELECT reader_id,name,lifecycle,published_revision FROM watch_profiles WHERE id=?",
                (profile_id,),
            ).fetchone()
            current = existing[3] if existing is not None else None
            if existing is not None:
                if (existing[0], existing[1]) != (data["reader_id"], data["name"]):
                    raise WatchConfigurationError("identity_conflict")
                if existing[2] == "archived":
                    raise WatchConfigurationError("profile_archived")
                previous = connection.execute(
                    "SELECT revision FROM watch_profile_revisions WHERE profile_id=? AND fingerprint=?",
                    (profile_id, document.fingerprint),
                ).fetchone()
                if previous is not None:
                    # A delayed replay returns its revision, never rolls back current.
                    return self._read(connection, profile_id, previous[0])
            if current != expected_revision:
                raise WatchConfigurationError("revision_conflict")
            for domain in data["domains"]:
                if (
                    connection.execute(
                        "SELECT 1 FROM domain_definitions WHERE id=? AND revision=?",
                        (domain["id"], domain["revision"]),
                    ).fetchone()
                    is None
                ):
                    raise WatchConfigurationError("domain_revision_missing", domain["id"])
            now = datetime.now(timezone.utc).isoformat()
            if existing is None:
                connection.execute(
                    "INSERT INTO watch_profiles(id,reader_id,name,lifecycle,created_at) "
                    "VALUES(?,?,?,'active',?)",
                    (profile_id, data["reader_id"], data["name"], now),
                )
            revision = (current or 0) + 1
            connection.execute(
                "INSERT INTO watch_profile_revisions("
                "profile_id,revision,scope_text,filters_json,fingerprint,published_at) VALUES(?,?,?,?,?,?)",
                (
                    profile_id,
                    revision,
                    data["scope_text"],
                    self._json(data["filters"]),
                    document.fingerprint,
                    now,
                ),
            )
            connection.executemany(
                "INSERT INTO watch_profile_domains(profile_id,revision,domain_id,domain_revision) "
                "VALUES(?,?,?,?)",
                [(profile_id, revision, d["id"], d["revision"]) for d in data["domains"]],
            )
            connection.execute(
                "UPDATE watch_profiles SET published_revision=? WHERE id=?", (revision, profile_id)
            )
            return self._read(connection, profile_id, revision)

    def _read(self, connection: sqlite3.Connection, profile_id: str, revision: int | None) -> ProfileRevision:
        row = connection.execute(
            "SELECT published_revision,lifecycle FROM watch_profiles WHERE id=?", (profile_id,)
        ).fetchone()
        if row is None or row[0] is None:
            raise WatchConfigurationError("profile_missing")
        selected = revision if revision is not None else row[0]
        detail = connection.execute(
            "SELECT fingerprint,scope_text,filters_json FROM watch_profile_revisions "
            "WHERE profile_id=? AND revision=?",
            (profile_id, selected),
        ).fetchone()
        if detail is None:
            raise WatchConfigurationError("revision_missing")
        domains = tuple(
            (value[0], value[1])
            for value in connection.execute(
                "SELECT domain_id,domain_revision FROM watch_profile_domains "
                "WHERE profile_id=? AND revision=? ORDER BY domain_id",
                (profile_id, selected),
            ).fetchall()
        )
        return ProfileRevision(profile_id, selected, row[0], detail[0], row[1], detail[1], detail[2], domains)

    def read(self, profile_id: str, revision: int | None = None) -> ProfileRevision:
        self._revision(revision)
        with self._transaction() as connection:
            return self._read(connection, profile_id, revision)

    def select_current_revision(
        self,
        profile_id: str,
        revision: int,
        *,
        expected_current_revision: int,
    ) -> ProfileRevision:
        self._revision(revision)
        self._revision(expected_current_revision)
        if (
            not isinstance(profile_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", profile_id) is None
        ):
            raise WatchConfigurationError("invalid_identifier")
        with self._transaction(write=True) as connection:
            current = connection.execute(
                "SELECT lifecycle,published_revision FROM watch_profiles "
                "WHERE id=?",
                (profile_id,),
            ).fetchone()
            if current is None or current[1] is None:
                raise WatchConfigurationError("profile_missing")
            if current[0] == "archived":
                raise WatchConfigurationError("profile_archived")
            if current[1] != expected_current_revision:
                raise WatchConfigurationError("revision_conflict")
            target = connection.execute(
                "SELECT 1 FROM watch_profile_revisions "
                "WHERE profile_id=? AND revision=?",
                (profile_id, revision),
            ).fetchone()
            if target is None:
                raise WatchConfigurationError("revision_missing")
            if revision != current[1]:
                updated = connection.execute(
                    "UPDATE watch_profiles SET published_revision=? "
                    "WHERE id=? AND published_revision=?",
                    (
                        revision,
                        profile_id,
                        expected_current_revision,
                    ),
                ).rowcount
                if updated != 1:
                    raise WatchConfigurationError("revision_conflict")
            return self._read(connection, profile_id, revision)

    def set_lifecycle(self, profile_id: str, lifecycle: str) -> None:
        if lifecycle not in {"active", "paused"}:
            raise WatchConfigurationError("invalid_lifecycle")
        with self._transaction(write=True) as connection:
            current = connection.execute(
                "SELECT lifecycle FROM watch_profiles WHERE id=?", (profile_id,)
            ).fetchone()
            if current is None:
                raise WatchConfigurationError("profile_missing")
            if current[0] == "archived":
                raise WatchConfigurationError("profile_archived")
            connection.execute("UPDATE watch_profiles SET lifecycle=? WHERE id=?", (lifecycle, profile_id))
