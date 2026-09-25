import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.delivery.dtos.delivery_dispatch import (
    DeliveryClaim,
    DeliveryDispatchCandidate,
    DeliveryPreflightItem,
    DeliveryPreflightSnapshot,
    MailSendResult,
)
from libs.delivery.dtos.delivery_queue import QueueDigestRequest, QueuedDigest
from libs.delivery.dtos.digest_preview import DigestPreview, SelectedDigestItem
from libs.delivery.exceptions.delivery_store_error import DeliveryStoreError


class SqliteDeliveryStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _text(value: object, code: str, *, maximum: int = 512) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
            raise DeliveryStoreError(code)
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise DeliveryStoreError(code) from None
        return value

    @staticmethod
    def _fingerprint(value: object, code: str) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise DeliveryStoreError(code)
        return value

    @staticmethod
    def _object_id(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"digest:[0-9a-f]{64}", value) is None:
            raise DeliveryStoreError("invalid_digest_object")
        return value

    @staticmethod
    def _time(value: object, code: str) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise DeliveryStoreError(code)
        try:
            return value.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            raise DeliveryStoreError(code) from None

    @staticmethod
    def _hash(prefix: str, *parts: str) -> str:
        payload = "\0".join((prefix, *parts)).encode("utf-8")
        return prefix + ":" + hashlib.sha256(payload).hexdigest()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.in_transaction:
                raise DeliveryStoreError("owned_connection_required")
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except DeliveryStoreError:
            raise
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "delivery_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "delivery_database_error"
            )
            raise DeliveryStoreError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def _validate(cls, request: QueueDigestRequest) -> tuple[DigestPreview, str, str]:
        if not isinstance(request, QueueDigestRequest) or not isinstance(request.preview, DigestPreview):
            raise DeliveryStoreError("invalid_delivery_request")
        preview = request.preview
        cls._text(preview.subscription_id, "invalid_subscription_id")
        cls._text(preview.period_key, "invalid_period_key")
        cutoff_at = cls._time(preview.cutoff_at, "invalid_cutoff")
        created_at = cls._time(request.created_at, "invalid_created_at")
        cls._text(request.reader_id, "invalid_reader_id")
        if request.channel not in {"email", "rss"}:
            raise DeliveryStoreError("invalid_delivery_channel")
        if type(request.workspace_epoch) is not int or request.workspace_epoch < 1:
            raise DeliveryStoreError("invalid_workspace_epoch")
        cls._object_id(request.rendered_object_id)
        if (
            preview.queueable is not True
            or not isinstance(preview.items, tuple)
            or not preview.items
            or not isinstance(preview.subject, str)
            or not preview.subject
            or not isinstance(preview.text_body, str)
            or not preview.text_body
            or not isinstance(preview.html_body, str)
            or not preview.html_body
        ):
            raise DeliveryStoreError("digest_not_queueable")
        cls._fingerprint(preview.content_fingerprint, "invalid_preview_fingerprint")
        if any(not isinstance(item, SelectedDigestItem) for item in preview.items):
            raise DeliveryStoreError("invalid_digest_items")
        paper_kinds = {
            "new_work",
            "late_discovery",
            "revision_available",
            "newly_accessible",
        }
        for item in preview.items:
            if item.item_kind == "paper":
                if (
                    item.event_kind not in paper_kinds
                    or not isinstance(item.summary_id, str)
                    or not isinstance(item.revision_id, str)
                ):
                    raise DeliveryStoreError("invalid_digest_items")
            elif item.item_kind == "status_notice":
                if (
                    item.event_kind not in {"correction", "retraction"}
                    or item.summary_id is not None
                    or item.revision_id is not None
                ):
                    raise DeliveryStoreError("invalid_digest_items")
            else:
                raise DeliveryStoreError("invalid_digest_items")
        if len({item.event_id for item in preview.items}) != len(preview.items):
            raise DeliveryStoreError("duplicate_digest_event")
        if request.rebuild_reason is None:
            if request.rebuild_outbox_id is not None:
                raise DeliveryStoreError("invalid_digest_rebuild_target")
        else:
            reason = cls._text(
                request.rebuild_reason,
                "invalid_digest_rebuild_reason",
                maximum=128,
            )
            if reason != "current_input_stale":
                raise DeliveryStoreError("invalid_digest_rebuild_reason")
            cls._text(
                request.rebuild_outbox_id,
                "invalid_digest_rebuild_target",
                maximum=256,
            )
        return preview, cutoff_at, created_at

    @classmethod
    def _require_subscription(
        cls,
        connection: sqlite3.Connection,
        request: QueueDigestRequest,
    ) -> None:
        row = connection.execute(
            "SELECT reader_id,channel FROM delivery_subscriptions WHERE id=?",
            (request.preview.subscription_id,),
        ).fetchone()
        if row is None:
            raise DeliveryStoreError("delivery_subscription_missing")
        if row["reader_id"] != request.reader_id or row["channel"] != request.channel:
            raise DeliveryStoreError("delivery_subscription_mismatch")

    @classmethod
    def _require_payload(
        cls,
        connection: sqlite3.Connection,
        object_id: str,
    ) -> str:
        row = connection.execute(
            "SELECT content_sha256,kind,state FROM object_registry WHERE object_id=?",
            (object_id,),
        ).fetchone()
        if row is None or row["kind"] != "digest" or row["state"] != "available":
            raise DeliveryStoreError("digest_object_unavailable")
        return cls._fingerprint(row["content_sha256"], "digest_object_corrupt")

    @staticmethod
    def _expected_items(preview: DigestPreview) -> tuple[tuple[object, ...], ...]:
        return tuple(
            (
                position,
                item.event_id,
                item.work_id,
                item.summary_id,
                item.revision_id,
                item.item_kind,
            )
            for position, item in enumerate(preview.items, start=1)
        )

    @classmethod
    def _replay(
        cls,
        connection: sqlite3.Connection,
        request: QueueDigestRequest,
        *,
        payload_sha256: str,
        cutoff_at: str,
    ) -> QueuedDigest | None:
        digest = connection.execute(
            "SELECT * FROM digests WHERE subscription_id=? AND period_key=?",
            (request.preview.subscription_id, request.preview.period_key),
        ).fetchone()
        if digest is None or digest["state"] == "cancelled":
            return None
        digest_id = digest["id"]
        if (
            digest["cutoff_at"],
            digest["rendered_object_id"],
        ) != (
            cutoff_at,
            request.rendered_object_id,
        ):
            raise DeliveryStoreError("digest_period_conflict")

        rows = connection.execute(
            "SELECT position,event_id,work_id,summary_id,revision_id,item_kind "
            "FROM digest_items WHERE digest_id=? ORDER BY position",
            (digest_id,),
        ).fetchall()
        if tuple(tuple(row) for row in rows) != cls._expected_items(request.preview):
            raise DeliveryStoreError("digest_period_conflict")

        outbox = connection.execute(
            "SELECT id,idempotency_key,payload_sha256 FROM delivery_outbox "
            "WHERE digest_id=?",
            (digest_id,),
        ).fetchone()
        expected_outbox_id = cls._hash("outbox", digest_id)
        expected_idempotency = cls._hash(
            "delivery-request",
            digest_id,
            payload_sha256,
        )
        if outbox is None or tuple(outbox) != (
            expected_outbox_id,
            expected_idempotency,
            payload_sha256,
        ):
            raise DeliveryStoreError("digest_period_conflict")

        ledgers = connection.execute(
            "SELECT event_id,outbox_id FROM notification_ledger "
            "WHERE reader_id=? AND channel=? AND event_id IN ("
            + ",".join("?" for _ in request.preview.items)
            + ")",
            (
                request.reader_id,
                request.channel,
                *(item.event_id for item in request.preview.items),
            ),
        ).fetchall()
        expected_ledger = {
            (item.event_id, expected_outbox_id)
            for item in request.preview.items
        }
        if {
            (row["event_id"], row["outbox_id"])
            for row in ledgers
        } != expected_ledger:
            raise DeliveryStoreError("digest_period_conflict")
        return QueuedDigest(
            digest_id,
            expected_outbox_id,
            expected_idempotency,
            payload_sha256,
            True,
        )

    @classmethod
    def _rebuild_cancelled(
        cls,
        connection: sqlite3.Connection,
        request: QueueDigestRequest,
        *,
        payload_sha256: str,
        cutoff_at: str,
        created_at: str,
    ) -> QueuedDigest | None:
        digest = connection.execute(
            "SELECT * FROM digests WHERE subscription_id=? AND period_key=?",
            (request.preview.subscription_id, request.preview.period_key),
        ).fetchone()
        if digest is None:
            if request.rebuild_reason is not None:
                raise DeliveryStoreError("digest_rebuild_target_missing")
            return None
        if digest["state"] != "cancelled":
            return None
        if request.rebuild_reason != "current_input_stale":
            raise DeliveryStoreError("digest_rebuild_not_authorized")

        digest_id = digest["id"]
        outbox = connection.execute(
            "SELECT * FROM delivery_outbox WHERE digest_id=?",
            (digest_id,),
        ).fetchone()
        if outbox is None or outbox["state"] != "cancelled":
            raise DeliveryStoreError("digest_rebuild_state_conflict")
        if outbox["id"] != request.rebuild_outbox_id:
            raise DeliveryStoreError("digest_rebuild_target_mismatch")
        if connection.execute(
            "SELECT 1 FROM delivery_attempts WHERE outbox_id=? LIMIT 1",
            (outbox["id"],),
        ).fetchone() is not None:
            raise DeliveryStoreError("digest_rebuild_attempt_exists")

        old_items = connection.execute(
            "SELECT count(*) FROM digest_items WHERE digest_id=?",
            (digest_id,),
        ).fetchone()[0]
        ledgers = connection.execute(
            "SELECT id,event_id,reader_id,channel,outbox_id,state "
            "FROM notification_ledger WHERE outbox_id=? ORDER BY event_id",
            (outbox["id"],),
        ).fetchall()
        if (
            type(old_items) is not int
            or old_items < 1
            or len(ledgers) != old_items
            or any(
                row["reader_id"] != request.reader_id
                or row["channel"] != request.channel
                or row["outbox_id"] != outbox["id"]
                or row["state"] != "cancelled"
                for row in ledgers
            )
        ):
            raise DeliveryStoreError("digest_rebuild_ledger_conflict")

        prior_object_id = cls._object_id(digest["rendered_object_id"])
        prior_payload = cls._require_payload(connection, prior_object_id)
        if prior_payload != outbox["payload_sha256"]:
            raise DeliveryStoreError("digest_rebuild_prior_payload_conflict")
        generation = connection.execute(
            "SELECT COALESCE(MAX(generation),0)+1 "
            "FROM delivery_digest_rebuilds WHERE digest_id=?",
            (digest_id,),
        ).fetchone()[0]
        if type(generation) is not int or generation < 1:
            raise DeliveryStoreError("digest_rebuild_history_corrupt")
        rebuild_id = cls._hash(
            "digest-rebuild",
            digest_id,
            str(generation),
            outbox["id"],
            prior_payload,
        )
        connection.execute(
            "INSERT INTO delivery_digest_rebuilds("
            "id,digest_id,outbox_id,generation,prior_rendered_object_id,"
            "prior_payload_sha256,prior_idempotency_key,prior_workspace_epoch,"
            "prior_cutoff_at,reason,recorded_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                rebuild_id,
                digest_id,
                outbox["id"],
                generation,
                prior_object_id,
                prior_payload,
                outbox["idempotency_key"],
                outbox["workspace_epoch"],
                digest["cutoff_at"],
                request.rebuild_reason,
                created_at,
            ),
        )

        new_idempotency = cls._hash(
            "delivery-request",
            digest_id,
            payload_sha256,
        )
        connection.execute(
            "DELETE FROM digest_items WHERE digest_id=?",
            (digest_id,),
        )
        connection.execute(
            "UPDATE digests SET cutoff_at=?,rendered_object_id=?,state='queued' "
            "WHERE id=? AND state='cancelled'",
            (cutoff_at, request.rendered_object_id, digest_id),
        )
        connection.execute(
            "UPDATE delivery_outbox SET idempotency_key=?,payload_sha256=?,"
            "state='pending',workspace_epoch=?,next_attempt_at=NULL "
            "WHERE id=? AND state='cancelled'",
            (
                new_idempotency,
                payload_sha256,
                request.workspace_epoch,
                outbox["id"],
            ),
        )

        for position, item in enumerate(request.preview.items, start=1):
            connection.execute(
                "INSERT INTO digest_items("
                "digest_id,position,event_id,work_id,summary_id,revision_id,item_kind"
                ") VALUES(?,?,?,?,?,?,?)",
                (
                    digest_id,
                    position,
                    item.event_id,
                    item.work_id,
                    item.summary_id,
                    item.revision_id,
                    item.item_kind,
                ),
            )
            prior = connection.execute(
                "SELECT id,outbox_id,state FROM notification_ledger "
                "WHERE reader_id=? AND event_id=? AND channel=?",
                (
                    request.reader_id,
                    item.event_id,
                    request.channel,
                ),
            ).fetchone()
            if prior is None:
                ledger_id = cls._hash(
                    "notification",
                    request.reader_id,
                    item.event_id,
                    request.channel,
                )
                connection.execute(
                    "INSERT INTO notification_ledger("
                    "id,reader_id,event_id,channel,outbox_id,state,created_at"
                    ") VALUES(?,?,?,?,?,'reserved',?)",
                    (
                        ledger_id,
                        request.reader_id,
                        item.event_id,
                        request.channel,
                        outbox["id"],
                        created_at,
                    ),
                )
            elif (
                prior["outbox_id"] == outbox["id"]
                and prior["state"] == "cancelled"
            ):
                connection.execute(
                    "UPDATE notification_ledger SET state='reserved' "
                    "WHERE id=? AND state='cancelled'",
                    (prior["id"],),
                )
            else:
                raise DeliveryStoreError("event_already_notified")

        return QueuedDigest(
            digest_id,
            outbox["id"],
            new_idempotency,
            payload_sha256,
            False,
        )

    @staticmethod
    def _reject_notified_events(
        connection: sqlite3.Connection,
        request: QueueDigestRequest,
    ) -> None:
        placeholders = ",".join("?" for _ in request.preview.items)
        row = connection.execute(
            "SELECT 1 FROM notification_ledger WHERE reader_id=? AND channel=? "
            f"AND event_id IN ({placeholders}) LIMIT 1",
            (
                request.reader_id,
                request.channel,
                *(item.event_id for item in request.preview.items),
            ),
        ).fetchone()
        if row is not None:
            raise DeliveryStoreError("event_already_notified")

    def queue(self, request: QueueDigestRequest) -> QueuedDigest:
        preview, cutoff_at, created_at = self._validate(request)
        assert preview.content_fingerprint is not None
        digest_id = self._hash(
            "digest-record",
            preview.subscription_id,
            preview.period_key,
            preview.content_fingerprint,
        )
        outbox_id = self._hash("outbox", digest_id)
        with self._transaction() as connection:
            self._require_subscription(connection, request)
            payload_sha256 = self._require_payload(connection, request.rendered_object_id)
            idempotency_key = self._hash("delivery-request", digest_id, payload_sha256)
            replay = self._replay(
                connection,
                request,
                payload_sha256=payload_sha256,
                cutoff_at=cutoff_at,
            )
            if replay is not None:
                return replay
            rebuilt = self._rebuild_cancelled(
                connection,
                request,
                payload_sha256=payload_sha256,
                cutoff_at=cutoff_at,
                created_at=created_at,
            )
            if rebuilt is not None:
                return rebuilt

            self._reject_notified_events(connection, request)
            connection.execute(
                "INSERT INTO digests("
                "id,subscription_id,period_key,cutoff_at,rendered_object_id,state,created_at"
                ") VALUES(?,?,?,?,?,'queued',?)",
                (
                    digest_id,
                    preview.subscription_id,
                    preview.period_key,
                    cutoff_at,
                    request.rendered_object_id,
                    created_at,
                ),
            )
            for position, item in enumerate(preview.items, start=1):
                connection.execute(
                    "INSERT INTO digest_items("
                    "digest_id,position,event_id,work_id,summary_id,revision_id,item_kind"
                    ") VALUES(?,?,?,?,?,?,?)",
                    (
                        digest_id,
                        position,
                        item.event_id,
                        item.work_id,
                        item.summary_id,
                        item.revision_id,
                        item.item_kind,
                    ),
                )
            connection.execute(
                "INSERT INTO delivery_outbox("
                "id,digest_id,idempotency_key,payload_sha256,state,workspace_epoch,created_at"
                ") VALUES(?,?,?,?,'pending',?,?)",
                (
                    outbox_id,
                    digest_id,
                    idempotency_key,
                    payload_sha256,
                    request.workspace_epoch,
                    created_at,
                ),
            )
            for item in preview.items:
                ledger_id = self._hash(
                    "notification",
                    request.reader_id,
                    item.event_id,
                    request.channel,
                )
                connection.execute(
                    "INSERT INTO notification_ledger("
                    "id,reader_id,event_id,channel,outbox_id,state,created_at"
                    ") VALUES(?,?,?,?,?,'reserved',?)",
                    (
                        ledger_id,
                        request.reader_id,
                        item.event_id,
                        request.channel,
                        outbox_id,
                        created_at,
                    ),
                )

            return QueuedDigest(digest_id, outbox_id, idempotency_key, payload_sha256, False)


    def load_preflight(self, outbox_id: str) -> DeliveryPreflightSnapshot:
        outbox_id = self._text(outbox_id, "invalid_outbox_id")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT o.id AS outbox_id,o.digest_id,o.state AS outbox_state,"
                "d.subscription_id,d.state AS digest_state,s.reader_id,s.channel,"
                "s.enabled FROM delivery_outbox o "
                "JOIN digests d ON d.id=o.digest_id "
                "JOIN delivery_subscriptions s ON s.id=d.subscription_id "
                "WHERE o.id=?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise DeliveryStoreError("delivery_outbox_missing")
            items = connection.execute(
                "SELECT i.event_id,i.work_id,i.summary_id,i.revision_id,"
                "i.item_kind,e.event_kind "
                "FROM digest_items i "
                "JOIN research_events e ON e.id=i.event_id AND e.work_id=i.work_id "
                "WHERE i.digest_id=? ORDER BY i.position",
                (row["digest_id"],),
            ).fetchall()
            if not items:
                raise DeliveryStoreError("delivery_digest_empty")
            result = []
            for item in items:
                event_id = self._text(item["event_id"], "delivery_digest_corrupt")
                work_id = self._text(item["work_id"], "delivery_digest_corrupt")
                event_kind = self._text(
                    item["event_kind"],
                    "delivery_digest_corrupt",
                    maximum=64,
                )
                item_kind = item["item_kind"]
                if item_kind == "paper":
                    if (
                        not isinstance(item["summary_id"], str)
                        or not item["summary_id"]
                        or not isinstance(item["revision_id"], str)
                        or not item["revision_id"]
                    ):
                        raise DeliveryStoreError("delivery_digest_corrupt")
                elif item_kind == "status_notice":
                    if (
                        item["summary_id"] is not None
                        or item["revision_id"] is not None
                        or event_kind not in {"correction", "retraction"}
                    ):
                        raise DeliveryStoreError("delivery_digest_corrupt")
                else:
                    raise DeliveryStoreError("delivery_digest_corrupt")
                result.append(
                    DeliveryPreflightItem(
                        event_id,
                        work_id,
                        item["summary_id"],
                        item["revision_id"],
                        item_kind,
                        event_kind,
                    )
                )
            return DeliveryPreflightSnapshot(
                row["outbox_id"],
                row["digest_id"],
                row["subscription_id"],
                row["reader_id"],
                row["channel"],
                bool(row["enabled"]),
                row["outbox_state"],
                row["digest_state"],
                tuple(result),
            )

    def cancel_pending(self, outbox_id: str) -> str:
        outbox_id = self._text(outbox_id, "invalid_outbox_id")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT o.state AS outbox_state,o.digest_id,d.state AS digest_state "
                "FROM delivery_outbox o JOIN digests d ON d.id=o.digest_id "
                "WHERE o.id=?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise DeliveryStoreError("delivery_outbox_missing")
            if row["outbox_state"] == "cancelled":
                return "cancelled"
            if row["outbox_state"] != "pending" or row["digest_state"] != "queued":
                return row["outbox_state"]
            attempt = connection.execute(
                "SELECT 1 FROM delivery_attempts WHERE outbox_id=? LIMIT 1",
                (outbox_id,),
            ).fetchone()
            if attempt is not None:
                raise DeliveryStoreError("delivery_cancel_attempt_exists")
            states = connection.execute(
                "SELECT DISTINCT state FROM notification_ledger WHERE outbox_id=?",
                (outbox_id,),
            ).fetchall()
            if not states or any(item["state"] != "reserved" for item in states):
                raise DeliveryStoreError("delivery_cancel_ledger_conflict")
            connection.execute(
                "UPDATE delivery_outbox SET state='cancelled' "
                "WHERE id=? AND state='pending'",
                (outbox_id,),
            )
            connection.execute(
                "UPDATE digests SET state='cancelled' WHERE id=? AND state='queued'",
                (row["digest_id"],),
            )
            connection.execute(
                "UPDATE notification_ledger SET state='cancelled' "
                "WHERE outbox_id=? AND state='reserved'",
                (outbox_id,),
            )
            return "cancelled"

    @staticmethod
    def _runtime_guard_available(connection: sqlite3.Connection) -> bool:
        versioned = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        required = {
            "digest_items",
            "current_summaries",
            "summary_revisions",
            "model_runs",
        }
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('digest_items','current_summaries','summary_revisions','model_runs')"
        ).fetchall()
        found = {row["name"] for row in rows}
        if versioned is None:
            return required <= found
        if found != required:
            raise DeliveryStoreError("delivery_schema_corrupt")
        return True

    @staticmethod
    def _paper_inputs_current(
        connection: sqlite3.Connection,
        digest_id: str,
    ) -> bool:
        rows = connection.execute(
            "SELECT i.work_id,i.summary_id,i.revision_id "
            "FROM digest_items i WHERE i.digest_id=? AND i.item_kind='paper'",
            (digest_id,),
        ).fetchall()
        for item in rows:
            current = connection.execute(
                "SELECT c.summary_id,c.revision_id,s.qa_state,"
                "c.expected_input_fingerprint,s.generation_fingerprint,"
                "m.state AS generation_state,m.input_fingerprint "
                "FROM current_summaries c "
                "JOIN summary_revisions s "
                "ON s.id=c.summary_id AND s.revision_id=c.revision_id "
                "AND s.work_id=c.work_id "
                "JOIN model_runs m ON m.id=s.generation_run_id "
                "WHERE c.work_id=? AND c.revision_id=? "
                "AND c.language='zh-TW' "
                "AND c.explanation_profile='plain-zh-TW-v1'",
                (item["work_id"], item["revision_id"]),
            ).fetchone()
            if (
                current is None
                or current["summary_id"] != item["summary_id"]
                or current["revision_id"] != item["revision_id"]
                or current["qa_state"] != "passed"
                or current["expected_input_fingerprint"]
                != current["generation_fingerprint"]
                or current["generation_state"] != "succeeded"
                or current["input_fingerprint"]
                != current["generation_fingerprint"]
            ):
                return False
        return True

    def load_dispatch(self, outbox_id: str) -> DeliveryDispatchCandidate:
        outbox_id = self._text(outbox_id, "invalid_outbox_id")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT o.id AS outbox_id,o.digest_id,o.idempotency_key,o.payload_sha256,"
                "o.workspace_epoch,o.state AS outbox_state,d.subscription_id,d.period_key,"
                "d.rendered_object_id,d.state AS digest_state,s.reader_id,s.channel,s.enabled,"
                "s.recipient_ref "
                "FROM delivery_outbox o "
                "JOIN digests d ON d.id=o.digest_id "
                "JOIN delivery_subscriptions s ON s.id=d.subscription_id "
                "WHERE o.id=?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise DeliveryStoreError("delivery_outbox_missing")
            return DeliveryDispatchCandidate(
                outbox_id=row["outbox_id"],
                digest_id=row["digest_id"],
                subscription_id=row["subscription_id"],
                period_key=row["period_key"],
                rendered_object_id=row["rendered_object_id"],
                idempotency_key=row["idempotency_key"],
                payload_sha256=row["payload_sha256"],
                workspace_epoch=row["workspace_epoch"],
                outbox_state=row["outbox_state"],
                digest_state=row["digest_state"],
                reader_id=row["reader_id"],
                channel=row["channel"],
                enabled=bool(row["enabled"]),
                recipient_ref=row["recipient_ref"],
            )

    def claim_dispatch(self, outbox_id: str, now: datetime) -> DeliveryClaim:
        outbox_id = self._text(outbox_id, "invalid_outbox_id")
        started_at = self._time(now, "invalid_dispatch_time")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT o.state AS outbox_state,o.workspace_epoch,o.digest_id,"
                "d.state AS digest_state,d.subscription_id,s.enabled,s.channel "
                "FROM delivery_outbox o "
                "JOIN digests d ON d.id=o.digest_id "
                "JOIN delivery_subscriptions s ON s.id=d.subscription_id "
                "WHERE o.id=?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise DeliveryStoreError("delivery_outbox_missing")

            state = row["outbox_state"]
            if state in {"provider_accepted", "unknown", "cancelled", "sending", "failed"}:
                return DeliveryClaim(state)

            if state != "pending" or row["digest_state"] != "queued":
                raise DeliveryStoreError("delivery_state_corrupt")
            if row["channel"] != "email":
                raise DeliveryStoreError("unsupported_delivery_channel")

            if not bool(row["enabled"]):
                connection.execute(
                    "UPDATE delivery_outbox SET state='cancelled' WHERE id=? AND state='pending'",
                    (outbox_id,),
                )
                connection.execute(
                    "UPDATE digests SET state='cancelled' WHERE id=?",
                    (row["digest_id"],),
                )
                connection.execute(
                    "UPDATE notification_ledger SET state='cancelled' WHERE outbox_id=?",
                    (outbox_id,),
                )
                return DeliveryClaim("cancelled")

            if (
                self._runtime_guard_available(connection)
                and not self._paper_inputs_current(connection, row["digest_id"])
            ):
                attempt = connection.execute(
                    "SELECT 1 FROM delivery_attempts WHERE outbox_id=? LIMIT 1",
                    (outbox_id,),
                ).fetchone()
                if attempt is not None:
                    raise DeliveryStoreError("delivery_state_corrupt")
                connection.execute(
                    "UPDATE delivery_outbox SET state='cancelled' "
                    "WHERE id=? AND state='pending'",
                    (outbox_id,),
                )
                connection.execute(
                    "UPDATE digests SET state='cancelled' "
                    "WHERE id=? AND state='queued'",
                    (row["digest_id"],),
                )
                connection.execute(
                    "UPDATE notification_ledger SET state='cancelled' "
                    "WHERE outbox_id=? AND state='reserved'",
                    (outbox_id,),
                )
                return DeliveryClaim("cancelled")

            workspace = connection.execute(
                "SELECT epoch,external_effects_enabled FROM workspace_metadata WHERE singleton=1"
            ).fetchone()
            if (
                workspace is None
                or not bool(workspace["external_effects_enabled"])
                or workspace["epoch"] != row["workspace_epoch"]
            ):
                return DeliveryClaim("effects_disabled")

            changed = connection.execute(
                "UPDATE delivery_outbox SET state='sending' WHERE id=? AND state='pending'",
                (outbox_id,),
            ).rowcount
            if changed != 1:
                return DeliveryClaim("sending")
            attempt_no = connection.execute(
                "SELECT COALESCE(MAX(attempt_no),0)+1 FROM delivery_attempts WHERE outbox_id=?",
                (outbox_id,),
            ).fetchone()[0]
            attempt_id = self._hash("delivery-attempt", outbox_id, str(attempt_no))
            connection.execute(
                "INSERT INTO delivery_attempts("
                "id,outbox_id,attempt_no,state,started_at"
                ") VALUES(?,?,?,'sending',?)",
                (attempt_id, outbox_id, attempt_no, started_at),
            )
            return DeliveryClaim("sending", attempt_id, attempt_no)


    def finish_dispatch(
        self,
        attempt_id: str,
        result: MailSendResult,
        finished_at: datetime,
    ) -> str:
        attempt_id = self._text(attempt_id, "invalid_attempt_id")
        finished = self._time(finished_at, "invalid_dispatch_time")
        if not isinstance(result, MailSendResult) or result.state not in {
            "provider_accepted",
            "rejected",
            "unknown",
        }:
            raise DeliveryStoreError("invalid_mail_result")
        if result.provider_message_id is not None:
            self._text(result.provider_message_id, "invalid_provider_message_id", maximum=1024)
        if result.error_code is not None:
            self._text(result.error_code, "invalid_delivery_error", maximum=256)

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT a.state AS attempt_state,a.outbox_id,o.state AS outbox_state,o.digest_id "
                "FROM delivery_attempts a JOIN delivery_outbox o ON o.id=a.outbox_id "
                "WHERE a.id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise DeliveryStoreError("delivery_attempt_missing")
            if row["attempt_state"] != "sending" or row["outbox_state"] != "sending":
                if row["attempt_state"] in {"provider_accepted", "failed", "unknown"}:
                    return row["outbox_state"]
                raise DeliveryStoreError("delivery_state_corrupt")

            if result.state == "provider_accepted":
                attempt_state = "provider_accepted"
                outbox_state = "provider_accepted"
                digest_state = "sent"
                ledger_state = "accepted"
            elif result.state == "rejected":
                attempt_state = "failed"
                outbox_state = "failed"
                digest_state = "queued"
                ledger_state = "reserved"
            else:
                attempt_state = "unknown"
                outbox_state = "unknown"
                digest_state = "unknown"
                ledger_state = "unknown"

            connection.execute(
                "UPDATE delivery_attempts SET state=?,provider_message_id=?,error_code=?,finished_at=? "
                "WHERE id=? AND state='sending'",
                (
                    attempt_state,
                    result.provider_message_id,
                    result.error_code,
                    finished,
                    attempt_id,
                ),
            )
            connection.execute(
                "UPDATE delivery_outbox SET state=? WHERE id=? AND state='sending'",
                (outbox_state, row["outbox_id"]),
            )
            connection.execute(
                "UPDATE digests SET state=? WHERE id=?",
                (digest_state, row["digest_id"]),
            )
            connection.execute(
                "UPDATE notification_ledger SET state=? WHERE outbox_id=?",
                (ledger_state, row["outbox_id"]),
            )
            return outbox_state
