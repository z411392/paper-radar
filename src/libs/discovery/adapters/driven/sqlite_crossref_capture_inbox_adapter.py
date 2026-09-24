"""Atomic bounded response+index staging; not HTTP authorization or a page checkpoint."""
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

from libs.discovery.domain.services.crossref_capture_inbox_rules import CrossrefCaptureInboxRules as Rules
from libs.discovery.domain.services.crossref_capture_rules import CrossrefCaptureRules as CaptureRules
from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_capture_inbox import CrossrefCaptureInboxRecord
from libs.discovery.exceptions.crossref_capture_inbox_error import CrossrefCaptureInboxError as Error


class SqliteCrossrefCaptureInboxAdapter:
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
                    raise Error('crossref_inbox_durable_journal_required')
                connection.execute('PRAGMA synchronous=EXTRA')
            else:
                connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            primary = getattr(exc, 'sqlite_errorcode', 0) & 0xff
            raise Error('crossref_inbox_database_busy' if primary in {5,6}
                        else 'crossref_inbox_database_error') from exc
        finally:
            if connection is not None:
                try:
                    if connection.in_transaction:
                        connection.rollback()
                finally:
                    connection.close()

    @staticmethod
    def _require_claim(connection: sqlite3.Connection, claim: CrossrefCaptureClaim) -> None:
        identity = Rules.identity(claim)
        row = connection.execute(
            'SELECT * FROM crossref_capture_claims WHERE id=?', (claim.claim_id,),
        ).fetchone()
        if row is None or any(row[k] != v for k,v in identity.items()):
            raise Error('crossref_inbox_claim_mismatch')
        latest = connection.execute('SELECT MAX(fencing_token) FROM crossref_capture_claims WHERE page_id=?',
                                    (claim.page_id,)).fetchone()[0]
        if row['state'] != 'dispatching' or latest != claim.fencing_token:
            raise Error('crossref_inbox_claim_not_dispatched')
        workspace = connection.execute('SELECT workspace_id,epoch FROM workspace_metadata WHERE singleton=1')
        if tuple(workspace.fetchone() or ()) != (claim.workspace_id,claim.workspace_epoch):
            raise Error('crossref_inbox_workspace_changed')
        # Saving already-received evidence is allowed after lease expiry, pass changes or
        # master-gate disablement; it never grants dispatch or permission to attach to a page.

    @staticmethod
    def _load(
        connection: sqlite3.Connection, claim: CrossrefCaptureClaim,
    ) -> CrossrefCaptureInboxRecord | None:
        sizes = connection.execute('SELECT length(envelope),length(body) FROM crossref_capture_inbox '
                                   'WHERE claim_id=?', (claim.claim_id,)).fetchone()
        if sizes is None:
            return None
        if (type(sizes[0]) is not int or not 1 <= sizes[0] <= CaptureRules.MAX_RECEIPT
                or type(sizes[1]) is not int or not 0 <= sizes[1] <= CaptureRules.MAX_BODY):
            raise Error('crossref_inbox_corrupt')
        row = connection.execute('SELECT envelope,envelope_sha256,body,body_sha256 '
                                 'FROM crossref_capture_inbox WHERE claim_id=?', (claim.claim_id,)).fetchone()
        return Rules.decode(claim, *tuple(row))

    def stage(self, claim: CrossrefCaptureClaim, capture: CrossrefHttpCapture, *,
              staged_at: datetime) -> CrossrefCaptureInboxRecord:
        envelope = Rules.encode(claim, capture)
        staged_us = Rules.instant_us(staged_at)
        result = Rules.decode(claim, envelope, CaptureRules.sha(envelope), capture.body,
                              CaptureRules.sha(capture.body))
        with self._transaction(write=True) as connection:
            self._require_claim(connection, claim)
            existing = self._load(connection, claim)
            if existing is not None:
                if existing != result:
                    raise Error('crossref_inbox_response_conflict')
                return existing
            connection.execute('INSERT INTO crossref_capture_inbox VALUES(?,?,?,?,?,?)',
                (claim.claim_id, envelope, result.envelope_sha256,
                 capture.body, result.body_sha256, staged_us))
            return result

    def load(self, claim: CrossrefCaptureClaim) -> CrossrefCaptureInboxRecord | None:
        Rules.identity(claim)
        with self._transaction(write=False) as connection:
            self._require_claim(connection, claim)
            return self._load(connection, claim)
