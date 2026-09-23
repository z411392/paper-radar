import sqlite3
from collections.abc import Callable

from libs.watch_profiles.dtos.digest_relevance import DigestRelevance
from libs.watch_profiles.exceptions.digest_relevance_read_error import (
    DigestRelevanceReadError,
)


class SqliteDigestRelevanceAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    def __call__(
        self,
        reader_id: str,
        revision_id: str,
    ) -> tuple[DigestRelevance, ...]:
        if not isinstance(reader_id, str) or not reader_id.strip():
            raise DigestRelevanceReadError("invalid_digest_reader")
        if not isinstance(revision_id, str) or not revision_id.strip():
            raise DigestRelevanceReadError("invalid_digest_revision")
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise DigestRelevanceReadError("owned_connection_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT d.domain_id,a.decision "
                "FROM relevance_assessments a "
                "JOIN relevance_assessment_domains d ON d.assessment_id=a.id "
                "JOIN watch_profiles p ON p.id=a.profile_id "
                "JOIN watch_profile_domains pd "
                "ON pd.profile_id=a.profile_id AND pd.revision=a.profile_revision "
                "AND pd.domain_id=d.domain_id AND pd.domain_revision=d.domain_revision "
                "WHERE p.reader_id=? AND p.lifecycle='active' "
                "AND p.published_revision=a.profile_revision "
                "AND a.revision_id=? AND a.execution_state='succeeded' "
                "AND a.decision IN ('direct','adjacent') "
                "ORDER BY d.domain_id,a.decision",
                (reader_id, revision_id),
            ).fetchall()
            connection.commit()
        except DigestRelevanceReadError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise DigestRelevanceReadError("digest_relevance_database_error") from exc
        finally:
            if connection is not None:
                connection.close()

        strongest: dict[str, str] = {}
        for row in rows:
            domain_id = row["domain_id"]
            decision = row["decision"]
            if not isinstance(domain_id, str) or decision not in {"direct", "adjacent"}:
                raise DigestRelevanceReadError("digest_relevance_corrupt")
            if strongest.get(domain_id) != "direct":
                strongest[domain_id] = decision
        return tuple(
            DigestRelevance(domain_id, strongest[domain_id])
            for domain_id in sorted(strongest)
        )
