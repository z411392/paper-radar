"""Canonical SQLite inbox with real receipt codec; object port is a disk fixture.

No provider transport is installed. Process-exit tests are not hardware power-loss tests.
"""
import hashlib
import sqlite3
import subprocess
import sys
import threading
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.adapters.driven.kernel_crossref_capture_store_adapter import (
    KernelCrossrefCaptureStoreAdapter,
)
from libs.discovery.adapters.driven.sqlite_crossref_capture_inbox_adapter import (
    SqliteCrossrefCaptureInboxAdapter,
)
from libs.discovery.application.commands.publish_claimed_crossref_capture import PublishClaimedCrossrefCapture
from libs.discovery.domain.services.crossref_capture_rules import CrossrefCaptureRules as Rules
from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.exceptions.crossref_capture_inbox_error import CrossrefCaptureInboxError as Error

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def us(value):
    delta = value - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


class Fixture:
    def __init__(self, root, *, initialize=True):
        self.root = Path(root)
        self.path = self.root / 'inbox.sqlite3'
        self.statements = []
        self.plan = CrossrefSourceAdapter().compile(CrossrefWindowInput(
            'binding:fixture', 'statistics', NOW-timedelta(days=2), NOW-timedelta(days=1),
            'fixture@example.invalid', 'v1', 2,
        ))
        request = CrossrefSourceAdapter().page(self.plan)
        self.claim = CrossrefCaptureClaim('claim:one', 'page:one', 'pass:one', 1, 'owner:one',
            'workspace:one', 1, request, NOW, NOW+timedelta(seconds=60), 'reserved')
        if initialize:
            repo = Path(__file__).resolve().parents[5]
            con = self.connect()
            for name in ('0001-object-registry.sql', '0011-crossref-harvest.sql',
                         '0014-crossref-capture-claims.sql', '0015-crossref-capture-inbox.sql'):
                con.executescript((repo / 'migrations' / name).read_text())
            con.execute("INSERT INTO workspace_metadata VALUES(1,'workspace:one',1,1,?,NULL)",
                        (NOW.isoformat(),))
            con.execute("INSERT INTO crossref_harvest_windows VALUES(?,?,?,?,?,?,?,'running',?,?)",
                ('window:one','binding:fixture',self.plan.query_fingerprint,'v1',
                 self.plan.definition.from_index.isoformat(),self.plan.definition.until_index.isoformat(),
                 2,NOW.isoformat(),NOW.isoformat()))
            con.execute("INSERT INTO crossref_harvest_passes "
                "(id,window_id,pass_no,parameters_fingerprint,state,current_cursor,started_at) "
                "VALUES('pass:one','window:one',1,?,'running','*',?)",
                (self.plan.parameters_fingerprint,NOW.isoformat()))
            con.execute("INSERT INTO crossref_harvest_pages "
                "(id,pass_id,page_no,cursor_in,request_fingerprint,state,created_at) "
                "VALUES('page:one','pass:one',0,'*',?,'requested',?)",
                (request.request_fingerprint,NOW.isoformat()))
            con.execute(
                "INSERT INTO crossref_capture_claims VALUES(?,?,?,?,?,?,?,?,?,?,'reserved',NULL,NULL)",
                (self.claim.claim_id,self.claim.page_id,self.claim.pass_id,1,self.claim.owner_id,
                 self.claim.workspace_id,1,Rules.canonical(asdict(request)).decode(),us(NOW),
                 us(self.claim.lease_until)))
            con.execute("UPDATE crossref_capture_claims SET state='dispatching',dispatched_us=?", (us(NOW),))
            con.close()
        self.inbox = SqliteCrossrefCaptureInboxAdapter(self.connect)
        self.captures = KernelCrossrefCaptureStoreAdapter(self.publish, self.read)
        self.publish_calls = 0
        self.read_calls = 0

    def connect(self):
        con = sqlite3.connect(self.path, isolation_level=None, timeout=2)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.set_trace_callback(self.statements.append)
        return con

    def sql(self, statement, values=()):
        con = self.connect()
        try:
            return con.execute(statement, values).fetchall()
        finally:
            con.close()

    def capture(self, **changes):
        return replace(CrossrefHttpCapture(200, (('content-type','application/json'),),
            b'{"items":[{"DOI":"10.1234/X"}]}', NOW+timedelta(seconds=2), True, None), **changes)

    def publish(self, body, kind, media_type, retention_policy):
        self.publish_calls += 1
        sha = hashlib.sha256(body).hexdigest()
        path = self.root/'objects'/sha
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(body)
        return SimpleNamespace(
            object_id='raw:'+sha, state='available', content_sha256=sha, byte_size=len(body),
        )

    def read(self, object_id):
        self.read_calls += 1
        return (self.root/'objects'/object_id[4:]).read_bytes()

    def stage(self, **changes):
        return self.inbox.stage(self.claim, self.capture(**changes), staged_at=NOW+timedelta(seconds=3))


@pytest.fixture
def fixture(tmp_path):
    return Fixture(tmp_path)


def test_indexed_stage_reopen_and_exact_replay(fixture):
    assert fixture.inbox.load(fixture.claim) is None
    first = fixture.stage()
    assert first == fixture.stage()
    reopened = Fixture(fixture.root, initialize=False)
    assert reopened.inbox.load(fixture.claim) == first
    assert first.capture == fixture.capture()
    assert len(fixture.sql('SELECT * FROM crossref_capture_inbox')) == 1
    assert fixture.sql('SELECT state FROM crossref_capture_claims')[0][0] == 'dispatching'
    assert fixture.sql('SELECT * FROM crossref_harvest_page_attempts') == []
    assert fixture.sql('SELECT current_cursor FROM crossref_harvest_passes')[0][0] == '*'


@pytest.mark.parametrize('changes', [dict(body=b'other'),dict(status=201),dict(headers=(('etag','x'),)),
    dict(received_at=NOW),dict(complete=False,capture_error='source_timeout')])
def test_same_claim_different_response_is_conflict_not_overwrite(fixture, changes):
    before = fixture.stage()
    with pytest.raises(Error, match='crossref_inbox_response_conflict'):
        fixture.stage(**changes)
    assert fixture.inbox.load(fixture.claim) == before


@pytest.mark.parametrize('field,value', [('owner_id','other'),('page_id','other'),('pass_id','other'),
    ('fencing_token',True),('workspace_epoch',True),('workspace_id','other'),
    ('lease_until',NOW+timedelta(hours=1)),('reserved_at',NOW+timedelta(seconds=1))])
def test_wrong_claim_never_stages_or_publishes(fixture, field, value):
    claim = replace(fixture.claim, **{field:value})
    with pytest.raises(Error):
        fixture.inbox.stage(claim, fixture.capture(), staged_at=NOW)
    assert fixture.sql('SELECT * FROM crossref_capture_inbox') == []


def test_tampered_request_keeps_fingerprint_but_is_rejected(fixture):
    claim = replace(
        fixture.claim, request=replace(fixture.claim.request, url=fixture.claim.request.url+'&extra=1'),
    )
    with pytest.raises(Error, match='crossref_inbox_claim_mismatch'):
        fixture.inbox.stage(claim, fixture.capture(), staged_at=NOW)


def test_workspace_restore_fences_stage_and_publication(fixture):
    fixture.stage()
    fixture.sql('UPDATE workspace_metadata SET epoch=2')
    with pytest.raises(Error, match='crossref_inbox_workspace_changed'):
        fixture.stage()
    with pytest.raises(Error, match='crossref_inbox_workspace_changed'):
        PublishClaimedCrossrefCapture(fixture.inbox,fixture.captures)(fixture.claim)
    assert fixture.publish_calls == 0


def test_disabled_master_gate_does_not_drop_already_received_evidence(fixture):
    fixture.sql('UPDATE workspace_metadata SET external_effects_enabled=0')
    assert fixture.stage().capture == fixture.capture()
    assert fixture.sql('SELECT external_effects_enabled FROM workspace_metadata')[0][0] == 0


def test_late_response_is_evidence_not_new_authorization(fixture):
    fixture.sql("UPDATE crossref_harvest_passes SET state='failed'")
    late = fixture.capture(received_at=NOW+timedelta(hours=2))
    saved = fixture.inbox.stage(fixture.claim, late, staged_at=NOW+timedelta(hours=2))
    assert saved.capture == late
    assert fixture.sql('SELECT state FROM crossref_capture_claims')[0][0] == 'dispatching'


@pytest.mark.parametrize('status,headers,body,complete,error', [
    (403,(),b'forbidden',True,None), (429,(('retry-after','3600'),),b'limited',True,None),
    (503,(),b'failed',True,None), (302,(('location','https://publisher.invalid'),),b'',True,None),
    (200,(('content-encoding','gzip'),),b'partial',False,'source_timeout'),
    (None,(),b'',False,'source_connection_error'),
])
def test_all_response_outcomes_are_preserved(fixture,status,headers,body,complete,error):
    capture=CrossrefHttpCapture(status,headers,body,NOW,complete,error)
    fixture.inbox.stage(fixture.claim,capture,staged_at=NOW)
    stored=PublishClaimedCrossrefCapture(fixture.inbox,fixture.captures)(fixture.claim)
    assert stored.capture==capture
    assert stored.attempt_key==fixture.claim.claim_id


def test_no_inbox_is_unknown_not_refetch(fixture):
    with pytest.raises(Error,match='crossref_inbox_response_missing'):
        PublishClaimedCrossrefCapture(fixture.inbox,fixture.captures)(fixture.claim)
    assert fixture.publish_calls == 0


@pytest.mark.parametrize('failure_call', [1,2])
def test_object_publication_crash_can_replay_from_inbox_without_recapture(fixture,failure_call):
    fixture.stage()
    def broken_publish(*args):
        result=fixture.publish(*args)
        if fixture.publish_calls==failure_call:
            raise OSError('crash after durable object')
        return result
    broken=KernelCrossrefCaptureStoreAdapter(broken_publish,fixture.read)
    with pytest.raises(Error,match='crossref_inbox_publication_failed'):
        PublishClaimedCrossrefCapture(fixture.inbox,broken)(fixture.claim)
    result=PublishClaimedCrossrefCapture(fixture.inbox,fixture.captures)(fixture.claim)
    assert result==PublishClaimedCrossrefCapture(fixture.inbox,fixture.captures)(fixture.claim)
    assert len(list((fixture.root/'objects').iterdir()))==2
    assert len(fixture.sql('SELECT * FROM crossref_capture_inbox'))==1


def test_sql_failure_rolls_back_entire_response(fixture):
    fixture.sql("CREATE TRIGGER fail_inbox AFTER INSERT ON crossref_capture_inbox "
                "BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(Error,match='crossref_inbox_database_error'):
        fixture.stage()
    assert fixture.sql('SELECT * FROM crossref_capture_inbox')==[]
    assert fixture.sql('SELECT state FROM crossref_capture_claims')[0][0]=='dispatching'


@pytest.mark.parametrize('statement', [
    "UPDATE crossref_capture_inbox SET body=X'00'", 'DELETE FROM crossref_capture_inbox',
])
def test_inbox_history_is_immutable(fixture,statement):
    fixture.stage()
    with pytest.raises(sqlite3.IntegrityError):
        fixture.sql(statement)


def test_concurrent_same_response_has_one_row(fixture):
    barrier=threading.Barrier(2)
    results=[]
    errors=[]
    def worker():
        try:
            barrier.wait(timeout=3)
            results.append(fixture.stage())
        except Exception as exc:
            errors.append(exc)
    threads=[threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert not any(t.is_alive() for t in threads)
    assert not errors and len(results)==2 and results[0]==results[1]
    assert len(fixture.sql('SELECT * FROM crossref_capture_inbox'))==1


def test_ten_thousand_old_raw_objects_are_not_scanned(fixture):
    con=fixture.connect()
    con.execute('BEGIN')
    for i in range(10000):
        sha=hashlib.sha256(str(i).encode()).hexdigest()
        con.execute('INSERT INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)',
            ('raw:'+sha,sha,'old/'+sha,'raw','application/octet-stream',1,'missing','bad-clock','old'))
    con.commit()
    con.close()
    fixture.statements.clear()
    fixture.stage()
    assert fixture.inbox.load(fixture.claim) is not None
    assert not any('object_registry' in s.lower() for s in fixture.statements)
    assert fixture.read_calls==0
    plans=fixture.sql(
        'EXPLAIN QUERY PLAN SELECT * FROM crossref_capture_inbox WHERE claim_id=?', ('claim:one',),
    )
    assert any('SEARCH' in p[3] and 'INDEX' in p[3] for p in plans)


def test_stage_commit_survives_abrupt_process_exit(fixture):
    code=('import os;from pathlib import Path;'
          'from libs.discovery.tests.integration.test_crossref_capture_inbox import Fixture;'
          f'f=Fixture(Path({str(fixture.root)!r}),initialize=False);f.stage();os._exit(37)')
    completed=subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=15)
    assert completed.returncode==37,completed.stderr.decode()
    assert fixture.inbox.load(fixture.claim).capture==fixture.capture()
    published=PublishClaimedCrossrefCapture(fixture.inbox,fixture.captures)(fixture.claim)
    assert published.capture==fixture.capture()


@pytest.mark.parametrize('changes', [dict(status=True),dict(complete=1),dict(body='not bytes'),
    dict(body=b'x'*(Rules.MAX_BODY+1)),dict(headers=['wrong']),dict(received_at=NOW.replace(tzinfo=None))])
def test_invalid_capture_does_not_create_inbox(fixture,changes):
    with pytest.raises(Error):
        fixture.stage(**changes)
    assert fixture.sql('SELECT * FROM crossref_capture_inbox')==[]


def test_sql_replace_cannot_overwrite_an_immutable_response(fixture):
    fixture.stage()
    # SQLite REPLACE can skip delete triggers when recursive_triggers is disabled.
    assert fixture.sql('PRAGMA recursive_triggers')[0][0] == 0
    with pytest.raises(sqlite3.IntegrityError):
        fixture.sql('INSERT OR REPLACE INTO crossref_capture_inbox '
                    'SELECT claim_id,envelope,envelope_sha256,body,body_sha256,staged_us+1 '
                    'FROM crossref_capture_inbox')


@pytest.mark.parametrize('state', ['reserved','released','expired'])
def test_only_database_dispatching_claim_can_stage(fixture,state):
    # Rebuild a valid S1 state without relaxing its production trigger.
    fixture.sql('DROP TRIGGER crossref_capture_claim_transition')
    fixture.sql("UPDATE crossref_capture_claims SET state='reserved',dispatched_us=NULL")
    if state != 'reserved':
        fixture.sql('UPDATE crossref_capture_claims SET state=?,ended_us=?',(state,us(NOW)))
    with pytest.raises(Error,match='crossref_inbox_claim_not_dispatched'):
        fixture.stage()
    with pytest.raises(sqlite3.IntegrityError):
        fixture.sql('INSERT INTO crossref_capture_inbox VALUES(?,?,?,?,?,?)',
                    ('claim:one',b'{}','a'*64,b'', 'b'*64,us(NOW)))


def test_concurrent_different_responses_have_one_winner_and_one_conflict(fixture):
    barrier=threading.Barrier(2)
    results=[]
    errors=[]
    def worker(body):
        try:
            barrier.wait(timeout=3)
            results.append(fixture.stage(body=body))
        except Exception as exc:
            errors.append(exc)
    threads=[threading.Thread(target=worker,args=(b,)) for b in (b'first',b'second')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert not any(t.is_alive() for t in threads)
    assert len(results)==len(errors)==1
    assert isinstance(errors[0],Error) and errors[0].code=='crossref_inbox_response_conflict'
    assert fixture.inbox.load(fixture.claim)==results[0]


@pytest.mark.parametrize('field,expression', [('body',"X'626164'"),('envelope',"X'7b7d'"),
    ('body_sha256',"'"+'f'*64+"'"),('envelope_sha256',"'"+'e'*64+"'")])
def test_corruption_is_detected_before_publication(fixture,field,expression):
    fixture.stage()
    fixture.sql('DROP TRIGGER crossref_inbox_immutable') # corruption injection only
    fixture.sql(f'UPDATE crossref_capture_inbox SET {field}={expression}')
    with pytest.raises(Error,match='crossref_inbox_corrupt'):
        PublishClaimedCrossrefCapture(fixture.inbox,fixture.captures)(fixture.claim)
    assert fixture.publish_calls==0


def test_epoch_change_during_object_publication_is_detected(fixture):
    fixture.stage()
    def racing_publish(*args):
        result=fixture.publish(*args)
        fixture.sql('UPDATE workspace_metadata SET epoch=2')
        return result
    captures=KernelCrossrefCaptureStoreAdapter(racing_publish,fixture.read)
    with pytest.raises(Error,match='crossref_inbox_workspace_changed'):
        PublishClaimedCrossrefCapture(fixture.inbox,captures)(fixture.claim)
    assert len(fixture.sql('SELECT * FROM crossref_capture_inbox'))==1
    assert fixture.sql('SELECT * FROM crossref_harvest_page_attempts')==[]


def test_wrong_publication_identity_is_rejected(fixture):
    fixture.stage()
    def forged_read(receipt_id):
        original=fixture.captures.read(receipt_id)
        return replace(original,attempt_key='other-claim')
    captures=SimpleNamespace(save=fixture.captures.save,read=forged_read)
    with pytest.raises(Error,match='crossref_inbox_publication_mismatch'):
        PublishClaimedCrossrefCapture(fixture.inbox,captures)(fixture.claim)


def test_object_io_does_not_hold_a_database_writer_lock(fixture):
    fixture.stage()
    def checking_publish(*args):
        con=fixture.connect()
        con.execute('PRAGMA busy_timeout=50')
        con.execute('BEGIN IMMEDIATE')
        con.rollback()
        con.close()
        return fixture.publish(*args)
    captures=KernelCrossrefCaptureStoreAdapter(checking_publish,fixture.read)
    PublishClaimedCrossrefCapture(fixture.inbox,captures)(fixture.claim)


def test_abrupt_exit_inside_stage_transaction_leaves_no_half_response(fixture):
    code=('import os;from pathlib import Path;'
          'from libs.discovery.tests.integration.test_crossref_capture_inbox import Fixture;'
          'from libs.discovery.adapters.driven.sqlite_crossref_capture_inbox_adapter import '
          'SqliteCrossrefCaptureInboxAdapter;'
          f'f=Fixture(Path({str(fixture.root)!r}),initialize=False);'
          'original=f.connect;'
          '\ndef connect():\n c=original();c.create_function("die",0,lambda:os._exit(38));return c\n'
          'f.sql("CREATE TRIGGER exit_stage AFTER INSERT ON crossref_capture_inbox BEGIN SELECT die(); END");'
          'f.inbox=SqliteCrossrefCaptureInboxAdapter(connect);f.stage()')
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=15)
    assert result.returncode==38,result.stderr.decode()
    assert fixture.inbox.load(fixture.claim) is None
    assert fixture.sql('SELECT state FROM crossref_capture_claims')[0][0]=='dispatching'


def test_malformed_claim_state_has_typed_error(fixture):
    with pytest.raises(Error,match='invalid_crossref_inbox_claim'):
        fixture.inbox.load(replace(fixture.claim,state=[]))


def test_sql_replace_cannot_rebind_a_claim_that_owns_inbox_evidence(fixture):
    fixture.stage()
    with pytest.raises(sqlite3.IntegrityError):
        fixture.sql('INSERT OR REPLACE INTO crossref_capture_claims '
            "SELECT id,page_id,pass_id,fencing_token,'other-owner',workspace_id,workspace_epoch,"
            'request_json,reserved_us,lease_until_us,state,dispatched_us,ended_us '
            'FROM crossref_capture_claims')
    assert fixture.inbox.load(fixture.claim).capture==fixture.capture()
