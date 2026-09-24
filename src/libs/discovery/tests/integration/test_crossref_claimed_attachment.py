"""Real v15 bootstrap/claim/inbox, disk object-port fixture; no provider I/O."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.adapters.driven.kernel_crossref_capture_store_adapter import (
    KernelCrossrefCaptureStoreAdapter,
)
from libs.discovery.adapters.driven.sqlite_claimed_crossref_attachment_adapter import (
    SqliteClaimedCrossrefAttachmentAdapter,
)
from libs.discovery.adapters.driven.sqlite_crossref_capture_claim_store_adapter import (
    SqliteCrossrefCaptureClaimStoreAdapter,
)
from libs.discovery.adapters.driven.sqlite_crossref_capture_inbox_adapter import (
    SqliteCrossrefCaptureInboxAdapter,
)
from libs.discovery.application.commands.attach_claimed_crossref_capture import AttachClaimedCrossrefCapture
from libs.discovery.application.commands.publish_claimed_crossref_capture import PublishClaimedCrossrefCapture
from libs.discovery.domain.services.crossref_rate_policy import CrossrefRatePolicy
from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.exceptions.crossref_attachment_error import CrossrefAttachmentError as Error
from libs.discovery.exceptions.crossref_capture_claim_error import CrossrefCaptureClaimError
from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError
from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter

NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


class Fixture:
    def __init__(self, root):
        self.root = root
        migrations = load_workspace_migrations(with_runtime=True)
        self.info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
        assert self.info.schema_version == 16
        self.connection = SqliteSchemaConnectionFactory(root, migrations, minimum_version=15)
        self.connect = self.connection.connect
        self.sql('UPDATE workspace_metadata SET external_effects_enabled=1')
        source = CrossrefSourceAdapter()
        self.plan = source.compile(CrossrefWindowInput(
            'binding:attachment', 'statistics', NOW-timedelta(days=2), NOW-timedelta(days=1),
            'fixture@example.invalid', 'v1', 2,
        ))
        self.request = source.page(self.plan)
        self.sql("INSERT INTO crossref_harvest_windows VALUES(?,?,?,?,?,?,?,'running',?,?)", (
            'window:test', self.plan.definition.binding_key, self.plan.query_fingerprint, 'v1',
            self.plan.definition.from_index.isoformat(), self.plan.definition.until_index.isoformat(),
            2, NOW.isoformat(), NOW.isoformat(),
        ))
        self.sql("INSERT INTO crossref_harvest_passes "
                 "(id,window_id,pass_no,parameters_fingerprint,state,current_cursor,started_at) "
                 "VALUES('pass:test','window:test',1,?,'running','*',?)",
                 (self.plan.parameters_fingerprint, NOW.isoformat()))
        self.claims = SqliteCrossrefCaptureClaimStoreAdapter(self.connect)
        self.claim = self.claims.reserve(
            self.plan, 'pass:test', self.request, owner_id='worker:test',
            expected_workspace_id=self.info.workspace_id, expected_epoch=self.info.epoch,
            now=NOW, lease_seconds=60,
        )
        self.claims.begin_dispatch(self.claim, now=NOW)
        self.inbox = SqliteCrossrefCaptureInboxAdapter(self.connect)
        self.captures = KernelCrossrefCaptureStoreAdapter(self.put, self.read)
        self.publisher = PublishClaimedCrossrefCapture(self.inbox, self.captures)
        self.store = SqliteClaimedCrossrefAttachmentAdapter(self.connect)

    def sql(self, query, values=()):
        c = self.connect()
        try:
            result = c.execute(query, values).fetchall()
            c.commit()
            return result
        finally:
            c.close()

    def put(self, content, kind, media_type, retention_policy):
        digest = hashlib.sha256(content).hexdigest()
        object_id = kind + ':' + digest
        path = self.root / 'attachment-fixture' / digest
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        self.sql('INSERT OR IGNORE INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)', (
            object_id, digest, 'attachment-fixture/'+digest, kind, media_type,
            len(content), 'available', NOW.isoformat(), retention_policy,
        ))
        return SimpleNamespace(object_id=object_id, content_sha256=digest,
                               byte_size=len(content), state='available')

    def read(self, object_id):
        row = self.sql('SELECT * FROM object_registry WHERE object_id=?', (object_id,))[0]
        assert row['state'] == 'available'
        data = (self.root / row['relative_path']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row['content_sha256']
        return data

    def stage(self, status=200, headers=(), complete=True, error=None):
        capture = CrossrefHttpCapture(status, headers, b'{"message":"fixture"}', NOW, complete, error)
        self.inbox.stage(self.claim, capture, staged_at=NOW)
        self.stored = self.publisher(self.claim)
        self.decision = CrossrefRatePolicy().evaluate(status, headers, now=NOW, capture_error=error)

    def attach(self):
        return self.store.attach(self.claim, self.stored, self.decision, attached_at=NOW)

    def before(self):
        return [tuple(tuple(r) for r in self.sql('SELECT * FROM '+table)) for table in (
            'crossref_harvest_page_attempts', 'crossref_harvest_pages', 'crossref_harvest_passes',
            'crossref_capture_claims', 'crossref_capture_inbox',
        )]


@pytest.fixture
def f(tmp_path):
    return Fixture(tmp_path / 'runtime')


def test_accept_without_advancing_cursor_or_releasing_claim(f):
    f.stage()
    result = f.attach()
    assert result.action == 'accept' and not result.replayed
    page = f.sql('SELECT * FROM crossref_harvest_pages')[0]
    assert page['state'] == 'captured' and page['successful_receipt_id'] == f.stored.receipt_id
    assert tuple(f.sql('SELECT current_cursor,next_page_no FROM crossref_harvest_passes')[0]) == ('*', 0)
    assert f.sql('SELECT state FROM crossref_capture_claims')[0][0] == 'dispatching'
    assert f.sql('SELECT * FROM crossref_harvest_items') == []
    with pytest.raises(CrossrefCaptureClaimError):
        f.claims.begin_dispatch(f.claim, now=NOW)


@pytest.mark.parametrize('status,headers,complete,error,action', [
    (403, (), True, None, 'stop'), (429, (('retry-after','3600'),), True, None, 'retry'),
    (503, (), True, None, 'retry'), (302, (('location','https://publisher.invalid'),), True, None, 'stop'),
    (200, (), False, 'crossref_response_incomplete', 'stop'),
])
def test_failure_receipt_does_not_become_success_or_resend_authority(
    f, status, headers, complete, error, action,
):
    f.stage(status, headers, complete, error)
    assert f.attach().action == action
    page = f.sql('SELECT * FROM crossref_harvest_pages')[0]
    assert page['state'] == 'requested' and page['successful_receipt_id'] is None
    assert page['last_error_code'] == f.decision.failure_code
    assert f.sql('SELECT state FROM crossref_capture_claims')[0][0] == 'dispatching'


def test_exact_replay_after_expiry_and_progress_is_readonly(f):
    f.stage()
    first = f.attach()
    f.sql("UPDATE crossref_harvest_pages SET state='accounted'")
    f.sql("UPDATE crossref_harvest_passes SET current_cursor='later',next_page_no=1")
    before = f.before()
    again = f.store.attach(f.claim, f.stored, f.decision, attached_at=NOW+timedelta(days=1))
    assert again.replayed and again.attempt_id == first.attempt_id
    assert f.before() == before


@pytest.mark.parametrize('field,value', [
    ('owner_id','wrong'), ('workspace_epoch',True), ('workspace_epoch',2), ('fencing_token',2),
    ('claim_id','wrong'), ('pass_id','wrong'), ('page_id','wrong'),
])
def test_forged_claim_never_attaches(f, field, value):
    f.stage()
    before = f.before()
    with pytest.raises(Error):
        f.store.attach(replace(f.claim, **{field:value}), f.stored, f.decision, attached_at=NOW)
    assert f.before() == before


@pytest.mark.parametrize('sql', [
    "UPDATE workspace_metadata SET epoch=epoch+1",
    "UPDATE crossref_harvest_passes SET state='failed'",
    "UPDATE crossref_harvest_passes SET current_cursor='later'",
    "UPDATE crossref_harvest_passes SET next_page_no=1",
    "UPDATE crossref_harvest_passes SET finished_at='2026-09-25T00:00:00+00:00'",
    "UPDATE crossref_harvest_windows SET state='failed'",
    "UPDATE crossref_harvest_pages SET state='decoded'",
    "UPDATE crossref_harvest_pages SET request_fingerprint='wrong'",
])
def test_changed_authority_rejected_without_mutation(f, sql):
    f.stage()
    f.sql(sql)
    before = f.before()
    with pytest.raises(Error):
        f.attach()
    assert f.before() == before


@pytest.mark.parametrize('delta', [-1, 60, 61])
def test_first_attach_after_expiry_or_clock_regression_rejected(f, delta):
    f.stage()
    before = f.before()
    with pytest.raises(Error):
        f.store.attach(f.claim, f.stored, f.decision, attached_at=NOW+timedelta(seconds=delta))
    assert f.before() == before


def test_master_off_allows_local_evidence_but_does_not_enable_http(f):
    f.stage()
    f.sql('UPDATE workspace_metadata SET external_effects_enabled=0')
    assert f.attach().action == 'accept'
    assert f.sql('SELECT external_effects_enabled FROM workspace_metadata')[0][0] == 0


@pytest.mark.parametrize('target', ['receipt', 'body'])
@pytest.mark.parametrize('state', ['missing', 'quarantined'])
def test_unavailable_objects_block_attachment(f, target, state):
    f.stage()
    object_id = f.stored.receipt_id if target == 'receipt' else f.stored.body_object_id
    f.sql('UPDATE object_registry SET state=? WHERE object_id=?', (state, object_id))
    before = f.before()
    with pytest.raises(Error):
        f.attach()
    assert f.before() == before


@pytest.mark.parametrize('change', ['receipt', 'body', 'capture', 'decision'])
def test_forged_publication_or_decision_rejected(f, change):
    f.stage()
    stored, decision = f.stored, f.decision
    if change == 'receipt':
        stored = replace(stored, receipt_id='raw:'+'f'*64)
    elif change == 'body':
        stored = replace(stored, body_sha256='f'*64)
    elif change == 'capture':
        stored = replace(stored, capture=replace(stored.capture, body=b'changed'))
    else:
        decision = replace(decision, action='retry', failure_code='invented')
    before = f.before()
    with pytest.raises(Error):
        f.store.attach(f.claim, stored, decision, attached_at=NOW)
    assert f.before() == before


def test_page_failure_rolls_back_attempt_insert(f):
    f.stage()
    f.sql("CREATE TRIGGER fail_attach BEFORE UPDATE ON crossref_harvest_pages "
          "BEGIN SELECT RAISE(ABORT,'injected'); END")
    before = f.before()
    with pytest.raises(Error):
        f.attach()
    assert f.before() == before
    f.sql('DROP TRIGGER fail_attach')
    assert not f.attach().replayed


def test_concurrent_same_attachment_creates_one_attempt(f):
    f.stage()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: f.attach(), range(2)))
    assert sorted(r.replayed for r in results) == [False, True]
    assert len(f.sql('SELECT * FROM crossref_harvest_page_attempts')) == 1


class Gate:
    def __init__(self, f, effect=None):
        self.f, self.effect, self.calls = f, effect, 0

    @contextmanager
    def slot(self, contact):
        assert contact == self.f.plan.definition.contact_email
        yield self

    def observe(self, status, headers, *, capture_error=None):
        self.calls += 1
        if self.effect:
            self.effect()
        return CrossrefRatePolicy().evaluate(status, headers, now=NOW, capture_error=capture_error)


def test_command_reconciles_before_attach_replay_does_not_reobserve(f):
    f.stage()
    gate = Gate(f)
    command = AttachClaimedCrossrefCapture(
        f.publisher, gate, f.store, source=CrossrefSourceAdapter(), clock=lambda: NOW,
    )
    first, second = command(f.plan, f.claim), command(f.plan, f.claim)
    assert not first.replayed and second.replayed and gate.calls == 1


def test_gate_race_revalidated_in_write_transaction(f):
    f.stage()
    gate = Gate(f, lambda: f.sql('UPDATE workspace_metadata SET epoch=epoch+1'))
    command = AttachClaimedCrossrefCapture(
        f.publisher, gate, f.store, source=CrossrefSourceAdapter(), clock=lambda: NOW,
    )
    with pytest.raises(Error):
        command(f.plan, f.claim)
    assert gate.calls == 1 and f.sql('SELECT * FROM crossref_harvest_page_attempts') == []


def test_gate_failure_preserves_inbox_without_attach(f):
    f.stage()
    def fail():
        raise CrossrefRateError('crossref_circuit_open')
    command = AttachClaimedCrossrefCapture(
        f.publisher, Gate(f, fail), f.store, source=CrossrefSourceAdapter(), clock=lambda: NOW,
    )
    with pytest.raises(CrossrefRateError, match='crossref_circuit_open'):
        command(f.plan, f.claim)
    assert len(f.sql('SELECT * FROM crossref_capture_inbox')) == 1
    assert f.sql('SELECT * FROM crossref_harvest_page_attempts') == []


def test_changed_contact_rejected_before_publication(f):
    class NoPublish:
        def __call__(self, claim):
            raise AssertionError('must validate first')
    changed = CrossrefSourceAdapter().compile(
        replace(f.plan.definition, contact_email='other@example.invalid'),
    )
    command = AttachClaimedCrossrefCapture(
        NoPublish(), Gate(f), f.store, source=CrossrefSourceAdapter(), clock=lambda: NOW,
    )
    with pytest.raises(Error):
        command(changed, f.claim)


def test_newer_pass_fences_old_attachment(f):
    f.stage()
    f.sql("INSERT INTO crossref_harvest_passes "
          "(id,window_id,pass_no,parameters_fingerprint,state,current_cursor,started_at) "
          "VALUES('pass:new','window:test',2,?,'running','*',?)",
          (f.plan.parameters_fingerprint, NOW.isoformat()))
    with pytest.raises(Error, match='page_not_active'):
        f.attach()
    assert f.sql('SELECT * FROM crossref_harvest_page_attempts') == []


@pytest.mark.parametrize('state', ['failed', 'traversed', 'repair_pending'])
def test_legitimate_repair_can_attach_but_finished_repair_cannot(f, state):
    f.stage()
    f.sql('UPDATE crossref_harvest_windows SET state=?', (state,))
    f.sql("INSERT INTO crossref_repair_runs VALUES('repair:test','window:test',1,"
          "'pass:test','operator','running',?,NULL,NULL)", (NOW.isoformat(),))
    before = f.before()
    f.sql("UPDATE crossref_repair_runs SET state='failed',finished_at=?", (NOW.isoformat(),))
    with pytest.raises(Error, match='page_not_active'):
        f.attach()
    assert f.before() == before
    f.sql("UPDATE crossref_repair_runs SET state='running',finished_at=NULL")
    assert f.attach().action == 'accept'
    assert f.sql('SELECT state FROM crossref_harvest_windows')[0][0] == state


def test_expired_during_gate_is_not_backdated_to_initial_time(f):
    f.stage()
    clock = [NOW]
    gate = Gate(f, lambda: clock.__setitem__(0, NOW+timedelta(seconds=60)))
    command = AttachClaimedCrossrefCapture(
        f.publisher, gate, f.store, source=CrossrefSourceAdapter(), clock=lambda: clock[0],
    )
    with pytest.raises(Error, match='lease_invalid'):
        command(f.plan, f.claim)
    assert gate.calls == 1 and f.sql('SELECT * FROM crossref_harvest_page_attempts') == []


def test_postcommit_epoch_change_still_rejects_replay(f):
    f.stage()
    f.attach()
    f.sql('UPDATE workspace_metadata SET epoch=epoch+1')
    with pytest.raises(Error, match='workspace_changed'):
        f.store.replay(f.claim, f.stored)


def test_existing_receipt_id_is_not_enough_without_inbox(f):
    capture = CrossrefHttpCapture(200, (), b'{}', NOW, True, None)
    receipt = f.captures.save(f.request, capture, attempt_key=f.claim.claim_id)
    stored = f.captures.read(receipt)
    decision = CrossrefRatePolicy().evaluate(200, (), now=NOW)
    with pytest.raises(Error, match='inbox_mismatch'):
        f.store.attach(f.claim, stored, decision, attached_at=NOW)


def test_real_shared_gate_403_remains_paused_after_replay(f):
    from libs.discovery.adapters.driven.posix_crossref_rate_gate_adapter import PosixCrossrefRateGateAdapter
    f.stage(403)
    directory = f.root / 'shared-gate'
    directory.mkdir(mode=0o700)
    gate = PosixCrossrefRateGateAdapter(directory, f.plan.definition.contact_email,
                                      clock=lambda: NOW.timestamp(), random_value=lambda: 0.5)
    command = AttachClaimedCrossrefCapture(
        f.publisher, gate, f.store, source=CrossrefSourceAdapter(), clock=lambda: NOW,
    )
    assert command(f.plan, f.claim).action == 'stop'
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert command(f.plan, f.claim).replayed
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    with pytest.raises(CrossrefRateError, match='crossref_circuit_open'):
        with gate.slot(f.plan.definition.contact_email):
            pytest.fail('must not reopen gate')


@pytest.mark.parametrize('when', ['before_commit', 'after_commit'])
def test_abrupt_process_exit_rolls_back_or_replays_once(f, when):
    import subprocess
    import sys
    from pathlib import Path
    f.stage()
    # JSON fixture describes an already dispatched, durable response; it is not a live request.
    from dataclasses import asdict
    info = f.root / 'attachment-restart.json'
    import json
    data = asdict(f.claim)
    data['reserved_at'] = f.claim.reserved_at.isoformat()
    data['lease_until'] = f.claim.lease_until.isoformat()
    info.write_text(json.dumps(data))
    script = r'''
import json,os,sqlite3,sys
from datetime import datetime
from pathlib import Path
from libs.discovery.adapters.driven.sqlite_claimed_crossref_attachment_adapter import (
    SqliteClaimedCrossrefAttachmentAdapter,
)
from libs.discovery.adapters.driven.kernel_crossref_capture_store_adapter import (
    KernelCrossrefCaptureStoreAdapter,
)
from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_page import CrossrefPageRequest
from libs.discovery.domain.services.crossref_rate_policy import CrossrefRatePolicy
from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
root=Path(sys.argv[1]); mode=sys.argv[2]; receipt=sys.argv[3]
factory=SqliteSchemaConnectionFactory(root,load_workspace_migrations(with_runtime=True),minimum_version=15)
def read(key):
    c=factory.connect()
    try: p=c.execute('SELECT relative_path FROM object_registry WHERE object_id=?',(key,)).fetchone()[0]
    finally: c.close()
    return (root/p).read_bytes()
d=json.loads((root/'attachment-restart.json').read_text())
d['request']=CrossrefPageRequest(**d['request'])
for key in ('reserved_at','lease_until'): d[key]=datetime.fromisoformat(d[key])
claim=CrossrefCaptureClaim(**d)
def connect():
    c=factory.connect()
    if mode=='before_commit':
        c.set_trace_callback(
            lambda sql: os._exit(53) if sql.startswith('UPDATE crossref_harvest_pages') else None
        )
    return c
store=KernelCrossrefCaptureStoreAdapter(None,read)
stored=store.read(receipt)
decision=CrossrefRatePolicy().evaluate(200,(),now=claim.reserved_at)
SqliteClaimedCrossrefAttachmentAdapter(connect).attach(claim,stored,decision,attached_at=claim.reserved_at)
os._exit(53)
'''
    process = subprocess.run([sys.executable, '-c', script, str(f.root), when, f.stored.receipt_id],
                             cwd=Path(__file__).resolve().parents[4], capture_output=True, timeout=15)
    assert process.returncode == 53, process.stdout + process.stderr
    result = f.attach()
    assert result.replayed is (when == 'after_commit')
    assert len(f.sql('SELECT * FROM crossref_harvest_page_attempts')) == 1
    assert f.sql('PRAGMA integrity_check')[0][0] == 'ok'


def test_application_depends_on_ports_not_driven_implementations():
    import inspect
    source = inspect.getsource(AttachClaimedCrossrefCapture)
    assert 'CrossrefSourceAdapter' not in source


@pytest.mark.parametrize('sql', [
    "UPDATE crossref_harvest_page_attempts SET id='forged'",
    "UPDATE crossref_harvest_page_attempts SET attempt_no=2",
])
def test_replay_does_not_trust_a_corrupted_attempt_identity(f, sql):
    f.stage()
    f.attach()
    f.sql(sql)
    with pytest.raises(Error, match='outcome_conflict'):
        f.store.replay(f.claim, f.stored)
