"""Atomically attach received evidence, never authorize HTTP or advance item accounting.

A successful attachment does not release an unresolved dispatch claim. Terminal/retry
claim resolution is a separate protocol. Object bytes must be read outside this adapter.
"""
import math
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.discovery.domain.services.crossref_capture_inbox_rules import (
    CrossrefCaptureInboxRules as InboxRules,
)
from libs.discovery.domain.services.crossref_capture_rules import CrossrefCaptureRules as Rules
from libs.discovery.domain.services.crossref_rate_policy import CrossrefRatePolicy
from libs.discovery.dtos.crossref_attachment import CrossrefAttachment
from libs.discovery.dtos.crossref_capture import CrossrefStoredCapture
from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_capture_resolution import CrossrefCaptureResolution
from libs.discovery.dtos.crossref_rate_decision import CrossrefRateDecision
from libs.discovery.exceptions.crossref_attachment_error import CrossrefAttachmentError as Error
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError
from libs.discovery.exceptions.crossref_capture_inbox_error import CrossrefCaptureInboxError
from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError


class SqliteClaimedCrossrefAttachmentAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise Error('owned_connection_required')
            if connection.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
                raise Error('foreign_keys_required')
            if write:
                if connection.execute('PRAGMA journal_mode').fetchone()[0] not in {
                    'wal', 'delete', 'truncate', 'persist',
                }:
                    raise Error('crossref_attachment_durable_journal_required')
                connection.execute('PRAGMA synchronous=EXTRA')
            else:
                connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            code = getattr(exc, 'sqlite_errorcode', 0) & 0xff
            raise Error('crossref_attachment_busy' if code in {5, 6}
                        else 'crossref_attachment_database_error') from exc
        finally:
            if connection is not None:
                try:
                    if connection.in_transaction:
                        connection.rollback()
                finally:
                    connection.close()

    @staticmethod
    def _evidence(claim, stored):
        try:
            identity = InboxRules.identity(claim)
            if (not isinstance(stored, CrossrefStoredCapture) or stored.attempt_key != claim.claim_id
                    or stored.request != claim.request):
                raise Error('crossref_attachment_evidence_mismatch')
            content = Rules.receipt_content(claim.request, stored.capture, attempt_key=claim.claim_id)
            body_sha = Rules.sha(stored.capture.body)
            if (stored.receipt_id != 'raw:' + Rules.sha(content)
                    or stored.body_sha256 != body_sha or stored.body_object_id != 'raw:' + body_sha):
                raise Error('crossref_attachment_evidence_mismatch')
            envelope = InboxRules.encode(claim, stored.capture)
            expected = CrossrefRatePolicy().evaluate(
                stored.capture.status, stored.capture.headers, now=stored.capture.received_at,
                capture_error=stored.capture.capture_error,
            )
        except (CrossrefCaptureInboxError, CrossrefCaptureError, CrossrefRateError) as exc:
            raise Error('crossref_attachment_evidence_mismatch') from exc
        return identity, content, envelope, expected

    @staticmethod
    def _proof(connection, claim, stored, proof, *, require_latest: bool = True):
        identity, content, envelope, _ = proof
        row = connection.execute('SELECT * FROM crossref_capture_claims WHERE id=?',
                                 (claim.claim_id,)).fetchone()
        latest = connection.execute('SELECT MAX(fencing_token) FROM crossref_capture_claims WHERE page_id=?',
                                    (claim.page_id,)).fetchone()[0]
        if (
            row is None
            or any(row[k] != v for k, v in identity.items())
            or row['state'] != 'dispatching'
            or (require_latest and latest != claim.fencing_token)
        ):
            raise Error('crossref_attachment_claim_fenced')
        workspace = connection.execute('SELECT workspace_id,epoch FROM workspace_metadata WHERE singleton=1')
        if tuple(workspace.fetchone() or ()) != (claim.workspace_id, claim.workspace_epoch):
            raise Error('crossref_attachment_workspace_changed')
        # Read only size/hash fields first; never allocate an unbounded damaged BLOB.
        inbox = connection.execute(
            'SELECT length(envelope),envelope_sha256,length(body),body_sha256,staged_us '
            'FROM crossref_capture_inbox WHERE claim_id=?', (claim.claim_id,),
        ).fetchone()
        if (inbox is None or tuple(inbox)[:4] != (
            len(envelope), Rules.sha(envelope), len(stored.capture.body), stored.body_sha256,
        )):
            raise Error('crossref_attachment_inbox_mismatch')
        actual = connection.execute('SELECT envelope,body FROM crossref_capture_inbox WHERE claim_id=?',
                                    (claim.claim_id,)).fetchone()
        if tuple(actual) != (envelope, stored.capture.body):
            raise Error('crossref_attachment_inbox_mismatch')
        objects = ((stored.receipt_id, content), (stored.body_object_id, stored.capture.body))
        for object_id, payload in objects:
            obj = connection.execute('SELECT kind,state,content_sha256,byte_size FROM object_registry '
                                     'WHERE object_id=?', (object_id,)).fetchone()
            if obj is None or tuple(obj) != ('raw', 'available', Rules.sha(payload), len(payload)):
                raise Error('crossref_attachment_object_unavailable')
        return row, inbox['staged_us']

    @staticmethod
    def _prior_resolution_count(connection, claim) -> int:
        value = connection.execute(
            'SELECT count(*) FROM crossref_capture_resolutions r '
            'JOIN crossref_capture_claims c ON c.id=r.claim_id '
            'WHERE c.page_id=? AND c.fencing_token<?',
            (claim.page_id, claim.fencing_token),
        ).fetchone()[0]
        if type(value) is not int or value < 0:
            raise Error('crossref_attachment_outcome_conflict')
        return value

    @staticmethod
    def _existing(
        connection, claim, stored, expected, claim_row, staged_us, *, attached_us: int | None = None,
    ):
        row = connection.execute('SELECT * FROM crossref_harvest_page_attempts WHERE receipt_id=?',
                                 (stored.receipt_id,)).fetchone()
        if row is None:
            return None
        # Historical replay validates the original authority interval, not today's lease.
        # A different timezone representation of the same instant remains valid.
        try:
            recorded_us = InboxRules.instant_us(datetime.fromisoformat(row['recorded_at']))
        except (ValueError, TypeError, OverflowError, CrossrefCaptureInboxError) as exc:
            raise Error('crossref_attachment_outcome_conflict') from exc
        dispatched_us = claim_row['dispatched_us']
        if (type(dispatched_us) is not int or type(staged_us) is not int
                or not max(claim_row['reserved_us'], dispatched_us, staged_us)
                <= recorded_us < claim_row['lease_until_us']
                or (attached_us is not None and recorded_us != attached_us)):
            raise Error('crossref_attachment_outcome_conflict')
        attempt_no = SqliteClaimedCrossrefAttachmentAdapter._prior_resolution_count(
            connection,
            claim,
        ) + 1
        count = connection.execute(
            'SELECT count(*) FROM crossref_harvest_page_attempts WHERE page_id=?',
            (claim.page_id,),
        ).fetchone()[0]
        if count != attempt_no:
            raise Error('crossref_attachment_outcome_conflict')
        expected_id = 'crossref-attempt:' + Rules.sha(Rules.canonical(
            (claim.page_id, attempt_no, stored.receipt_id),
        ))
        if (row['id'] != expected_id or row['attempt_no'] != attempt_no
                or row['page_id'] != claim.page_id or row['action'] != expected.action
                or row['failure_code'] != expected.failure_code):
            raise Error('crossref_attachment_outcome_conflict')
        page = connection.execute('SELECT * FROM crossref_harvest_pages WHERE id=?',
                                  (claim.page_id,)).fetchone()
        if (page is None or page['pass_id'] != claim.pass_id
                or page['request_fingerprint'] != claim.request.request_fingerprint
                or page['cursor_in'] != claim.request.cursor):
            raise Error('crossref_attachment_page_mismatch')
        if row['action'] == 'accept' and (
            page['successful_receipt_id'] != stored.receipt_id
            or page['state'] not in {'captured', 'decoded', 'accounted'}
        ):
            raise Error('crossref_attachment_outcome_conflict')
        if row['action'] != 'accept' and (
            page['state'] != 'requested' or page['successful_receipt_id'] is not None
            or page['last_error_code'] != row['failure_code']
        ):
            raise Error('crossref_attachment_outcome_conflict')
        return CrossrefAttachment(claim.claim_id, claim.page_id, stored.receipt_id,
                                  row['id'], row['action'], row['failure_code'], True)

    @staticmethod
    def _active(
        connection, claim, row, staged_us, now_us, *,
        attached: CrossrefStoredCapture | None = None, expected: CrossrefRateDecision | None = None,
    ):
        if (type(row['dispatched_us']) is not int or now_us < max(row['dispatched_us'], staged_us)
                or now_us >= row['lease_until_us']):
            raise Error('crossref_attachment_lease_invalid')
        state = connection.execute(
            'SELECT p.*,w.query_fingerprint,w.state AS window_state FROM crossref_harvest_passes p '
            'JOIN crossref_harvest_windows w ON w.id=p.window_id WHERE p.id=?', (claim.pass_id,),
        ).fetchone()
        if state is None:
            raise Error('crossref_attachment_page_not_active')
        latest = connection.execute('SELECT MAX(pass_no) FROM crossref_harvest_passes WHERE window_id=?',
                                    (state['window_id'],)).fetchone()[0]
        page = connection.execute('SELECT * FROM crossref_harvest_pages WHERE id=?',
                                  (claim.page_id,)).fetchone()
        if (state['state'] != 'running' or state['finished_at'] is not None
                or state['pass_no'] != latest or state['query_fingerprint'] != claim.request.query_fingerprint
                or state['parameters_fingerprint'] != claim.request.parameters_fingerprint
                or state['current_cursor'] != claim.request.cursor or page is None
                or page['pass_id'] != claim.pass_id or page['page_no'] != state['next_page_no']
                or page['cursor_in'] != claim.request.cursor
                or page['request_fingerprint'] != claim.request.request_fingerprint):
            raise Error('crossref_attachment_page_not_active')
        if connection.execute('SELECT 1 FROM crossref_window_splits WHERE parent_window_id=?',
                              (state['window_id'],)).fetchone() is not None:
            raise Error('crossref_attachment_page_not_active')
        repairs = connection.execute('SELECT state,finished_at FROM crossref_repair_runs '
                                     'WHERE pass_id=? AND window_id=?',
                                     (claim.pass_id, state['window_id'])).fetchall()
        if repairs:
            if len(repairs) != 1 or tuple(repairs[0]) != ('running', None):
                raise Error('crossref_attachment_page_not_active')
        elif state['window_state'] != 'running':
            raise Error('crossref_attachment_page_not_active')
        prior_resolutions = SqliteClaimedCrossrefAttachmentAdapter._prior_resolution_count(
            connection,
            claim,
        )
        attempt_count = connection.execute(
            'SELECT count(*) FROM crossref_harvest_page_attempts WHERE page_id=?',
            (claim.page_id,),
        ).fetchone()[0]
        if attached is None:
            if page['state'] != 'requested' or page['successful_receipt_id'] is not None:
                raise Error('crossref_attachment_page_not_active')
            if attempt_count != prior_resolutions:
                raise Error('crossref_attachment_outcome_conflict')
        else:
            if attempt_count != prior_resolutions + 1:
                raise Error('crossref_attachment_outcome_conflict')
            if expected is None:
                raise Error('crossref_attachment_decision_mismatch')
            success = expected.action == 'accept'
            if (page['state'] != ('captured' if success else 'requested')
                    or page['successful_receipt_id'] != (attached.receipt_id if success else None)
                    or page['last_error_code'] != expected.failure_code):
                raise Error('crossref_attachment_outcome_conflict')

    def replay(
        self, claim: CrossrefCaptureClaim, stored: CrossrefStoredCapture,
    ) -> CrossrefAttachment | None:
        proof = self._evidence(claim, stored)
        with self._transaction(write=False) as connection:
            row, staged_us = self._proof(connection, claim, stored, proof)
            return self._existing(connection, claim, stored, proof[3], row, staged_us)

    def attach(self, claim: CrossrefCaptureClaim, stored: CrossrefStoredCapture,
               decision: CrossrefRateDecision, *, attached_at: datetime) -> CrossrefAttachment:
        proof = self._evidence(claim, stored)
        expected = proof[3]
        if (not isinstance(decision, CrossrefRateDecision)
                or (decision.action, decision.failure_code) != (expected.action, expected.failure_code)):
            raise Error('crossref_attachment_decision_mismatch')
        try:
            now_us = InboxRules.instant_us(attached_at)
        except CrossrefCaptureInboxError as exc:
            raise Error('invalid_crossref_attachment_time') from exc
        with self._transaction(write=True) as connection:
            row, staged_us = self._proof(connection, claim, stored, proof)
            prior = self._existing(connection, claim, stored, expected, row, staged_us)
            if prior is not None:
                return prior
            self._active(connection, claim, row, staged_us, now_us)
            attempt_no = self._prior_resolution_count(connection, claim) + 1
            attempt_id = 'crossref-attempt:' + Rules.sha(Rules.canonical(
                (claim.page_id, attempt_no, stored.receipt_id),
            ))
            inserted = connection.execute(
                'INSERT INTO crossref_harvest_page_attempts VALUES(?,?,?,?,?,?,?)',
                (attempt_id, claim.page_id, attempt_no, stored.receipt_id, decision.action,
                 decision.failure_code, Rules.instant(attached_at).isoformat()),
            ).rowcount
            if inserted != 1:
                raise Error('crossref_attachment_outcome_conflict')
            if decision.action == 'accept':
                changed = connection.execute("UPDATE crossref_harvest_pages SET state='captured',"
                    'successful_receipt_id=?,last_error_code=NULL WHERE id=? AND state=\'requested\'',
                    (stored.receipt_id, claim.page_id)).rowcount
            else:
                changed = connection.execute('UPDATE crossref_harvest_pages SET last_error_code=? '
                    "WHERE id=? AND state='requested'", (decision.failure_code, claim.page_id)).rowcount
            if changed != 1:
                raise Error('crossref_attachment_page_not_active')
            # A statement count is not a proof of the final joined state. Re-read inside
            # the same writer transaction; any inconsistency rolls back both writes.
            final_claim, final_staged = self._proof(connection, claim, stored, proof)
            self._active(connection, claim, final_claim, final_staged, now_us,
                         attached=stored, expected=expected)
            verified = self._existing(connection, claim, stored, expected, final_claim, final_staged,
                                      attached_us=now_us)
            if verified is None or verified.attempt_id != attempt_id:
                raise Error('crossref_attachment_outcome_conflict')
            return CrossrefAttachment(claim.claim_id, claim.page_id, stored.receipt_id,
                                      attempt_id, decision.action, decision.failure_code, False)


    def resolve(
        self,
        claim: CrossrefCaptureClaim,
        stored: CrossrefStoredCapture,
        *,
        resolved_at: datetime,
    ) -> CrossrefCaptureResolution:
        proof = self._evidence(claim, stored)
        try:
            resolved_us = InboxRules.instant_us(resolved_at)
        except CrossrefCaptureInboxError as exc:
            raise Error('invalid_crossref_resolution_time') from exc
        with self._transaction(write=True) as connection:
            claim_row, staged_us = self._proof(
                connection,
                claim,
                stored,
                proof,
                require_latest=False,
            )
            attempt_no = self._prior_resolution_count(connection, claim) + 1
            attempt = connection.execute(
                'SELECT * FROM crossref_harvest_page_attempts WHERE receipt_id=?',
                (stored.receipt_id,),
            ).fetchone()
            expected_attempt_id = 'crossref-attempt:' + Rules.sha(Rules.canonical(
                (claim.page_id, attempt_no, stored.receipt_id),
            ))
            if (
                attempt is None
                or attempt['id'] != expected_attempt_id
                or attempt['attempt_no'] != attempt_no
                or attempt['page_id'] != claim.page_id
                or attempt['action'] != proof[3].action
                or attempt['failure_code'] != proof[3].failure_code
            ):
                raise Error('crossref_attachment_not_attached')
            try:
                recorded_us = InboxRules.instant_us(
                    datetime.fromisoformat(attempt['recorded_at'])
                )
            except (
                ValueError,
                TypeError,
                OverflowError,
                CrossrefCaptureInboxError,
            ) as exc:
                raise Error('crossref_attachment_resolution_conflict') from exc
            dispatched_us = claim_row['dispatched_us']
            if (
                type(dispatched_us) is not int
                or type(staged_us) is not int
                or resolved_us < max(dispatched_us, staged_us, recorded_us)
            ):
                raise Error('crossref_attachment_resolution_time')

            decision = CrossrefRatePolicy().evaluate(
                stored.capture.status,
                stored.capture.headers,
                now=resolved_at,
                capture_error=stored.capture.capture_error,
            )
            if (
                decision.action != proof[3].action
                or decision.failure_code != proof[3].failure_code
            ):
                raise Error('crossref_attachment_resolution_conflict')
            retry_us = None
            retry_at = None
            if decision.action == 'retry':
                retry_us = resolved_us + math.ceil(
                    decision.delay_seconds * 1_000_000
                )
                if retry_us <= resolved_us:
                    raise Error('crossref_attachment_resolution_conflict')
                retry_at = datetime.fromtimestamp(
                    retry_us / 1_000_000,
                    tz=timezone.utc,
                )

            existing = connection.execute(
                'SELECT * FROM crossref_capture_resolutions WHERE claim_id=?',
                (claim.claim_id,),
            ).fetchone()
            expected = (
                claim.claim_id,
                attempt['id'],
                stored.receipt_id,
                decision.action,
                decision.failure_code,
                resolved_us,
                retry_us,
                'crossref-resolution-v1',
            )
            if existing is not None:
                if tuple(existing) != expected:
                    raise Error('crossref_attachment_resolution_conflict')
                return CrossrefCaptureResolution(
                    claim.claim_id,
                    attempt['id'],
                    stored.receipt_id,
                    decision.action,
                    decision.failure_code,
                    resolved_at,
                    retry_at,
                    'crossref-resolution-v1',
                    True,
                )

            latest = connection.execute(
                'SELECT MAX(fencing_token) FROM crossref_capture_claims WHERE page_id=?',
                (claim.page_id,),
            ).fetchone()[0]
            if latest != claim.fencing_token:
                raise Error('crossref_attachment_claim_fenced')
            inserted = connection.execute(
                'INSERT INTO crossref_capture_resolutions VALUES(?,?,?,?,?,?,?,?)',
                expected,
            ).rowcount
            if inserted != 1:
                raise Error('crossref_attachment_resolution_conflict')
            written = connection.execute(
                'SELECT * FROM crossref_capture_resolutions WHERE claim_id=?',
                (claim.claim_id,),
            ).fetchone()
            if written is None or tuple(written) != expected:
                raise Error('crossref_attachment_resolution_conflict')
            return CrossrefCaptureResolution(
                claim.claim_id,
                attempt['id'],
                stored.receipt_id,
                decision.action,
                decision.failure_code,
                resolved_at,
                retry_at,
                'crossref-resolution-v1',
                False,
            )
