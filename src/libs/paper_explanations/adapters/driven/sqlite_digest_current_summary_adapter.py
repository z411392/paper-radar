import json
import sqlite3
from collections.abc import Callable

from libs.kernel.ports.read_object_port import ReadObjectPort
from libs.paper_explanations.dtos.digest_current_summary import DigestCurrentSummary
from libs.paper_explanations.exceptions.digest_summary_read_error import DigestSummaryReadError


class SqliteDigestCurrentSummaryAdapter:
    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        read_object: ReadObjectPort,
    ) -> None:
        self._connect = connect
        self._read_object = read_object

    @staticmethod
    def _strict_object(content: bytes) -> dict[str, object]:
        if not isinstance(content, bytes) or not content or len(content) > 4 * 1024 * 1024:
            raise DigestSummaryReadError("digest_summary_artifact_corrupt")

        def pairs(rows: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in rows:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        try:
            data = json.loads(
                content.decode("utf-8"),
                object_pairs_hook=pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            raise DigestSummaryReadError("digest_summary_artifact_corrupt") from None
        if not isinstance(data, dict):
            raise DigestSummaryReadError("digest_summary_artifact_corrupt")
        return data

    @staticmethod
    def _text(value: object) -> str:
        if not isinstance(value, str) or not value.strip() or "\0" in value:
            raise DigestSummaryReadError("digest_summary_artifact_corrupt")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise DigestSummaryReadError("digest_summary_artifact_corrupt") from None
        return value

    def __call__(
        self,
        work_id: str,
        revision_id: str,
    ) -> DigestCurrentSummary | None:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise DigestSummaryReadError("owned_connection_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT c.summary_id,c.revision_id,c.expected_input_fingerprint,"
                "s.work_id,s.snapshot_id,s.generation_fingerprint,s.output_object_id,"
                "s.qa_state,s.language,s.explanation_profile "
                "FROM current_summaries c "
                "JOIN summary_revisions s "
                "ON s.id=c.summary_id AND s.revision_id=c.revision_id "
                "AND s.work_id=c.work_id "
                "WHERE c.work_id=? AND c.revision_id=? "
                "AND c.language='zh-TW' AND c.explanation_profile='plain-zh-TW-v1'",
                (work_id, revision_id),
            ).fetchone()
            connection.commit()
        except DigestSummaryReadError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise DigestSummaryReadError("digest_summary_database_error") from exc
        finally:
            if connection is not None:
                connection.close()

        if row is None:
            return None
        if (
            row["qa_state"] != "passed"
            or row["language"] != "zh-TW"
            or row["explanation_profile"] != "plain-zh-TW-v1"
            or row["work_id"] != work_id
            or row["revision_id"] != revision_id
            or row["expected_input_fingerprint"] != row["generation_fingerprint"]
        ):
            raise DigestSummaryReadError("digest_summary_corrupt")

        data = self._strict_object(self._read_object(row["output_object_id"]))
        required = {
            "format_version",
            "snapshot_id",
            "revision_id",
            "work_id",
            "generation_fingerprint",
            "generation_input_fingerprint",
            "language",
            "explanation_profile",
            "evidence_level",
            "original_abstract",
            "faithful_translation",
            "plain_language_card",
            "not_reported_in_read_evidence",
            "claim_ids",
        }
        if (
            set(data) != required
            or data["format_version"] != 1
            or data["snapshot_id"] != row["snapshot_id"]
            or data["revision_id"] != revision_id
            or data["work_id"] != work_id
            or data["generation_fingerprint"] != row["generation_fingerprint"]
            or data["language"] != "zh-TW"
            or data["explanation_profile"] != "plain-zh-TW-v1"
        ):
            raise DigestSummaryReadError("digest_summary_artifact_mismatch")

        cards = data["plain_language_card"]
        if not isinstance(cards, list) or len(cards) > 64:
            raise DigestSummaryReadError("digest_summary_artifact_corrupt")
        lines = []
        for card in cards:
            if (
                not isinstance(card, dict)
                or set(card) != {"claim_type", "text", "claim_ids"}
                or not isinstance(card["claim_type"], str)
                or not isinstance(card["claim_ids"], list)
            ):
                raise DigestSummaryReadError("digest_summary_artifact_corrupt")
            lines.append(self._text(card["text"]))
        return DigestCurrentSummary(
            work_id,
            revision_id,
            row["summary_id"],
            tuple(lines),
        )
