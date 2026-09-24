"""Author tests: real SQLite v16, production claims/inbox, disk object-port fixture.

No provider transport. Completed evidence is not itself a send authorization.
"""
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest

from libs.discovery.tests.integration.test_crossref_claimed_attachment import Fixture, NOW
from libs.discovery.dtos.crossref_capture_resolution import CrossrefCaptureResolution
from libs.discovery.application.commands.resolve_claimed_crossref_capture import (
    ResolveClaimedCrossrefCapture,
)
from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.exceptions.crossref_attachment_error import CrossrefAttachmentError
from libs.discovery.exceptions.crossref_capture_claim_error import CrossrefCaptureClaimError


@pytest.fixture
def f(tmp_path):
    fixture = Fixture(tmp_path / 'runtime')
    assert fixture.info.schema_version == 16
    assert fixture.sql('SELECT MAX(version) FROM schema_migrations')[0][0] == 16
    return fixture


def resolve(f, at=NOW):
    return f.store.resolve(f.claim, f.stored, resolved_at=at)


def reserve(f, at=NOW, owner='worker:retry'):
    return f.claims.reserve(f.plan, f.claim.pass_id, f.request, owner_id=owner,
        expected_workspace_id=f.info.workspace_id, expected_epoch=f.info.epoch,
        now=at, lease_seconds=60)


@pytest.mark.parametrize(
    'status,action',
    [(200,'accept'),(403,'stop'),(429,'retry'),(503,'retry'),(302,'stop')],
)
def test_resolution_preserves_claim_and_evidence(f, status, action):
    f.stage(status=status)
    attached = f.attach()
    before = f.before()
    result = resolve(f)
    assert isinstance(result, CrossrefCaptureResolution)
    assert (result.claim_id, result.receipt_id, result.attempt_id, result.action) == (
        f.claim.claim_id, f.stored.receipt_id, attached.attempt_id, action)
    assert not result.replayed
    assert f.before() == before
    assert (result.retry_not_before is not None) == (action == 'retry')
    assert f.sql('SELECT state FROM crossref_capture_claims')[0][0] == 'dispatching'


def test_no_attachment_or_unknown_outcome_does_not_authorize_retry(f):
    f.stage(status=503)
    with pytest.raises(CrossrefAttachmentError, match='not_attached'):
        resolve(f)
    with pytest.raises(CrossrefCaptureClaimError):
        reserve(f, NOW+timedelta(days=1))
    assert f.sql('SELECT * FROM crossref_capture_resolutions') == []


@pytest.mark.parametrize('status', [200,403,302])
def test_terminal_result_never_reopens_same_page(f, status):
    f.stage(status=status)
    f.attach()
    resolve(f)
    with pytest.raises(CrossrefCaptureClaimError):
        reserve(f, NOW+timedelta(days=1))
    assert len(f.sql('SELECT * FROM crossref_capture_claims')) == 1


def test_retry_wait_and_second_attachment_use_new_token_and_attempt(f):
    f.stage(status=429, headers=(('retry-after','3600'),))
    first = f.attach()
    result = resolve(f)
    assert result.retry_not_before == NOW+timedelta(hours=1)
    with pytest.raises(CrossrefCaptureClaimError, match='retry_not_due'):
        reserve(f, result.retry_not_before-timedelta(microseconds=1))
    old = f.claim
    next_claim = reserve(f, result.retry_not_before)
    assert next_claim.fencing_token == 2 and next_claim.owner_id == 'worker:retry'
    assert f.claims.read(old.claim_id).state == 'dispatching'
    f.claims.begin_dispatch(next_claim, now=result.retry_not_before)
    f.claim = next_claim
    # The next HTTP result is staged at the current attempt's time, not fixture's original NOW.
    capture = replace(f.stored.capture, status=200, headers=(), received_at=result.retry_not_before)
    f.inbox.stage(next_claim, capture, staged_at=result.retry_not_before)
    f.stored = f.publisher(next_claim)
    from libs.discovery.domain.services.crossref_rate_policy import CrossrefRatePolicy
    decision = CrossrefRatePolicy().evaluate(200, (), now=result.retry_not_before)
    second = f.store.attach(next_claim, f.stored, decision, attached_at=result.retry_not_before)
    assert second.attempt_id != first.attempt_id
    attempts = f.sql(
        'SELECT attempt_no FROM crossref_harvest_page_attempts ORDER BY attempt_no'
    )
    assert [row[0] for row in attempts] == [1,2]
    final = resolve(f, result.retry_not_before)
    assert final.action == 'accept'
    assert len(f.sql('SELECT * FROM crossref_capture_inbox')) == 2
    assert len(f.sql('SELECT * FROM crossref_capture_resolutions')) == 2


def test_resolution_replay_after_lease_expiry_does_not_extend_wait(f):
    f.stage(status=429, headers=(('retry-after','3600'),))
    f.attach()
    first = resolve(f, NOW+timedelta(seconds=2))
    before = tuple(tuple(row) for row in f.sql('SELECT * FROM crossref_capture_resolutions'))
    replay = resolve(f, NOW+timedelta(days=1))
    assert replay.replayed and replay.retry_not_before == first.retry_not_before
    assert tuple(tuple(row) for row in f.sql('SELECT * FROM crossref_capture_resolutions')) == before


def test_already_attached_result_can_be_resolved_after_lease_expired(f):
    f.stage(status=503)
    f.attach()
    result = resolve(f, NOW+timedelta(hours=1))
    assert result.action == 'retry' and result.retry_not_before > NOW+timedelta(hours=1)


def test_unattached_result_cannot_use_resolution_to_bypass_expired_lease(f):
    f.stage(status=503)
    with pytest.raises(CrossrefAttachmentError):
        f.store.attach(f.claim, f.stored, f.decision, attached_at=NOW+timedelta(minutes=1))
    with pytest.raises(CrossrefAttachmentError, match='not_attached'):
        resolve(f, NOW+timedelta(hours=1))


def test_master_off_can_record_evidence_but_not_reserve_retry(f):
    f.stage(status=503)
    f.attach()
    f.sql('UPDATE workspace_metadata SET external_effects_enabled=0')
    result = resolve(f)
    assert result.action == 'retry'
    with pytest.raises(CrossrefCaptureClaimError, match='effects_disabled'):
        reserve(f, result.retry_not_before)


def test_epoch_change_blocks_resolution_replay_and_retry(f):
    f.stage(status=503)
    f.attach()
    result = resolve(f)
    f.sql('UPDATE workspace_metadata SET epoch=epoch+1')
    with pytest.raises(CrossrefAttachmentError, match='workspace_changed'):
        resolve(f)
    with pytest.raises(CrossrefCaptureClaimError, match='workspace_changed'):
        reserve(f, result.retry_not_before)


@pytest.mark.parametrize('field,value', [('owner_id','other'),('fencing_token',99),('workspace_id','other')])
def test_forged_claim_cannot_resolve(f, field, value):
    f.stage(status=503)
    f.attach()
    with pytest.raises(CrossrefAttachmentError):
        f.store.resolve(replace(f.claim, **{field:value}), f.stored, resolved_at=NOW)
    assert f.sql('SELECT * FROM crossref_capture_resolutions') == []


def test_resolution_rejects_time_before_attachment(f):
    f.stage(status=503)
    f.store.attach(f.claim, f.stored, f.decision, attached_at=NOW+timedelta(seconds=2))
    with pytest.raises(CrossrefAttachmentError, match='resolution_time'):
        resolve(f)


@pytest.mark.parametrize('statement', [
    'UPDATE crossref_capture_resolutions SET action=\'accept\'',
    'DELETE FROM crossref_capture_resolutions',
    'INSERT OR REPLACE INTO crossref_capture_resolutions SELECT * FROM crossref_capture_resolutions',
])
def test_outcome_is_immutable_including_replace(f, statement):
    f.stage(status=503)
    f.attach()
    resolve(f)
    with pytest.raises(sqlite3.IntegrityError):
        f.sql(statement)
    assert len(f.sql('SELECT * FROM crossref_capture_resolutions')) == 1


def test_early_direct_sql_claim_is_blocked(f):
    f.stage(status=429, headers=(('retry-after','3600'),))
    f.attach()
    resolve(f)
    row = dict(f.sql('SELECT * FROM crossref_capture_claims')[0])
    row.update(id='forged:next', fencing_token=2, owner_id='other', state='reserved',
               dispatched_us=None, ended_us=None)
    with pytest.raises(sqlite3.IntegrityError):
        placeholders = ','.join('?' for _ in row)
        f.sql(
            'INSERT INTO crossref_capture_claims VALUES(' + placeholders + ')',
            tuple(row.values()),
        )


def test_claim_replace_cannot_remove_prior_dispatch(f):
    f.stage(status=503)
    f.attach()
    resolve(f)
    with pytest.raises(sqlite3.IntegrityError):
        f.sql('INSERT OR REPLACE INTO crossref_capture_claims SELECT * FROM crossref_capture_claims')


def test_resolution_rollback_leaves_unknown_claim_unretriable(f):
    f.stage(status=503)
    f.attach()
    f.sql("CREATE TRIGGER injected AFTER INSERT ON crossref_capture_resolutions "
          "BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(CrossrefAttachmentError):
        resolve(f)
    assert f.sql('SELECT * FROM crossref_capture_resolutions') == []
    with pytest.raises(CrossrefCaptureClaimError):
        reserve(f, NOW+timedelta(days=1))
    f.sql('DROP TRIGGER injected')
    assert resolve(f).action == 'retry'


def test_concurrent_resolution_and_retry_claim_are_serialized(f):
    f.stage(status=503)
    f.attach()
    gate = Barrier(2)
    def finish():
        gate.wait(timeout=5)
        return resolve(f)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: finish(), range(2)))
    assert {v.replayed for v in outcomes} == {True,False}
    gate = Barrier(2)
    def claim(index):
        gate.wait(timeout=5)
        try:
            return reserve(f, outcomes[0].retry_not_before, f'worker:{index}')
        except CrossrefCaptureClaimError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))
    assert sum(not isinstance(result, str) for result in results) == 1
    assert sum(row['state']=='reserved' for row in f.sql('SELECT * FROM crossref_capture_claims')) == 1


def test_unknown_previous_dispatch_still_blocks_under_new_schema(f):
    with pytest.raises(CrossrefCaptureClaimError, match='outcome_unknown'):
        reserve(f, NOW+timedelta(days=10))
    assert f.sql('SELECT * FROM crossref_capture_resolutions') == []


def test_new_reservation_rechecks_attempt_integrity(f):
    f.stage(status=503)
    f.attach()
    result = resolve(f)
    f.sql('UPDATE crossref_harvest_page_attempts SET attempt_no=9')
    with pytest.raises(CrossrefCaptureClaimError):
        reserve(f, result.retry_not_before)


def test_retry_must_recheck_current_pass_after_resolution(f):
    f.stage(status=503)
    f.attach()
    result = resolve(f)
    f.sql("UPDATE crossref_harvest_passes SET state='failed'")
    with pytest.raises(CrossrefCaptureClaimError, match='not_active'):
        reserve(f, result.retry_not_before)


def test_resolution_cannot_be_forged_to_shorten_provider_wait(f):
    f.stage(status=429, headers=(('retry-after','3600'),))
    attached = f.attach()
    from libs.discovery.domain.services.crossref_capture_inbox_rules import CrossrefCaptureInboxRules
    us = CrossrefCaptureInboxRules.instant_us(NOW)
    # Simulate an erroneous direct writer; the application must independently validate the deadline.
    f.sql('INSERT INTO crossref_capture_resolutions VALUES(?,?,?,?,?,?,?,?)', (
        f.claim.claim_id, attached.attempt_id, attached.receipt_id, 'retry',
        attached.failure_code, us, us+1, 'crossref-resolution-v1',
    ))
    with pytest.raises(CrossrefCaptureClaimError, match='retry_history_corrupt'):
        reserve(f, NOW+timedelta(seconds=1))
    with pytest.raises(CrossrefAttachmentError, match='resolution_conflict'):
        resolve(f, NOW+timedelta(seconds=1))


def test_historical_resolution_replays_after_new_claim_without_reopening_it(f):
    f.stage(status=503)
    f.attach()
    result = resolve(f)
    new = reserve(f, result.retry_not_before)
    before = f.before()
    replay = resolve(f, result.retry_not_before)
    assert replay.replayed and replay.retry_not_before == result.retry_not_before
    assert f.before() == before
    assert f.claims.read(new.claim_id).state == 'reserved'


def test_retry_count_is_bounded_without_deleting_receipts(f):
    from libs.discovery.adapters.driven.sqlite_crossref_capture_claim_store_adapter import (
        SqliteCrossrefCaptureClaimStoreAdapter,
    )
    f.stage(status=503)
    f.attach()
    result = resolve(f)
    f.claims = SqliteCrossrefCaptureClaimStoreAdapter(f.connect, max_dispatch_attempts=1)
    with pytest.raises(CrossrefCaptureClaimError, match='retry_exhausted'):
        reserve(f, result.retry_not_before)
    assert len(f.sql('SELECT * FROM crossref_capture_resolutions')) == 1
    assert len(f.sql('SELECT * FROM crossref_capture_inbox')) == 1


@pytest.mark.parametrize('maximum', [True,0,-1,1.5,101])
def test_retry_limit_rejects_invalid_configuration(f, maximum):
    from libs.discovery.adapters.driven.sqlite_crossref_capture_claim_store_adapter import (
        SqliteCrossrefCaptureClaimStoreAdapter,
    )
    with pytest.raises(CrossrefCaptureClaimError, match='invalid_crossref_retry_limit'):
        SqliteCrossrefCaptureClaimStoreAdapter(f.connect, max_dispatch_attempts=maximum)


def test_wait_check_repeats_at_begin_dispatch(f):
    f.stage(status=429, headers=(('retry-after','3600'),))
    f.attach()
    result = resolve(f)
    new = reserve(f, result.retry_not_before)
    with pytest.raises(CrossrefCaptureClaimError):
        f.claims.begin_dispatch(new, now=result.retry_not_before-timedelta(microseconds=1))
    assert f.claims.read(new.claim_id).state == 'reserved'


def test_unavailable_prior_evidence_blocks_retry(f):
    f.stage(status=503)
    f.attach()
    result = resolve(f)
    f.sql('UPDATE object_registry SET state=\'quarantined\' WHERE object_id=?', (f.stored.receipt_id,))
    with pytest.raises(CrossrefCaptureClaimError, match='retry_history_corrupt'):
        reserve(f, result.retry_not_before)


def test_resolution_requires_existing_outcome_even_with_damaged_attempt_id(f):
    f.stage(status=503)
    f.attach()
    f.sql("UPDATE crossref_harvest_page_attempts SET id='corrupt'")
    with pytest.raises(CrossrefAttachmentError):
        resolve(f)
    assert f.sql('SELECT * FROM crossref_capture_resolutions') == []


def test_released_unused_retry_reservation_keeps_prior_retry_authority(f):
    f.stage(status=503)
    f.attach()
    first = resolve(f)
    second = reserve(f, first.retry_not_before, owner='worker:reserved')
    f.claims.release(second, now=first.retry_not_before)
    third = reserve(
        f,
        first.retry_not_before + timedelta(seconds=1),
        owner='worker:third',
    )

    assert third.fencing_token == 3
    f.claims.begin_dispatch(
        third,
        now=first.retry_not_before + timedelta(seconds=1),
    )
    assert f.claims.read(third.claim_id).state == 'dispatching'


def test_expired_unused_retry_reservation_keeps_prior_retry_authority(f):
    f.stage(status=503)
    f.attach()
    first = resolve(f)
    second = reserve(f, first.retry_not_before, owner='worker:reserved')
    third_at = first.retry_not_before + timedelta(seconds=61)
    third = reserve(f, third_at, owner='worker:third')

    assert f.claims.read(second.claim_id).state == 'expired'
    assert third.fencing_token == 3
    f.claims.begin_dispatch(third, now=third_at)
    assert f.claims.read(third.claim_id).state == 'dispatching'


def test_historical_failure_attachment_replays_after_later_success(f):
    f.stage(status=503)
    first_claim = f.claim
    first_stored = f.stored
    first_decision = f.decision
    first_attachment = f.attach()
    first_resolution = resolve(f)

    second = reserve(f, first_resolution.retry_not_before)
    f.claims.begin_dispatch(second, now=first_resolution.retry_not_before)
    f.claim = second
    capture = replace(
        first_stored.capture,
        status=200,
        headers=(),
        received_at=first_resolution.retry_not_before,
    )
    f.inbox.stage(second, capture, staged_at=first_resolution.retry_not_before)
    f.stored = f.publisher(second)
    from libs.discovery.domain.services.crossref_rate_policy import CrossrefRatePolicy
    f.decision = CrossrefRatePolicy().evaluate(
        200,
        (),
        now=first_resolution.retry_not_before,
    )
    f.store.attach(
        second,
        f.stored,
        f.decision,
        attached_at=first_resolution.retry_not_before,
    )
    resolve(f, first_resolution.retry_not_before)

    replay = f.store.attach(
        first_claim,
        first_stored,
        first_decision,
        attached_at=first_resolution.retry_not_before + timedelta(days=1),
    )

    assert replay.replayed
    assert replay.attempt_id == first_attachment.attempt_id


def test_unexplained_later_attempt_still_blocks_historical_replay(f):
    f.stage(status=503)
    first_claim = f.claim
    first_stored = f.stored
    first_decision = f.decision
    first = f.attach()
    resolve(f)
    f.sql(
        'INSERT INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)',
        (
            'raw:' + 'f' * 64,
            'f' * 64,
            'fixture/unexplained',
            'raw',
            'application/json',
            1,
            'available',
            NOW.isoformat(),
            'source-response',
        ),
    )
    f.sql(
        'INSERT INTO crossref_harvest_page_attempts VALUES(?,?,?,?,?,?,?)',
        (
            'unexplained',
            first_claim.page_id,
            2,
            'raw:' + 'f' * 64,
            'retry',
            'crossref_server_error',
            NOW.isoformat(),
        ),
    )

    with pytest.raises(CrossrefAttachmentError, match='outcome_conflict'):
        f.store.attach(
            first_claim,
            first_stored,
            first_decision,
            attached_at=NOW + timedelta(days=1),
        )
    assert first.attempt_id != 'unexplained'


def test_resolution_application_is_local_and_uses_published_evidence(f):
    f.stage(status=503)
    f.attach()
    command = ResolveClaimedCrossrefCapture(
        f.publisher,
        f.store,
        source=CrossrefSourceAdapter(),
        clock=lambda: NOW,
    )

    result = command(f.plan, f.claim)

    assert result.action == 'retry'
    assert len(f.sql('SELECT * FROM crossref_capture_resolutions')) == 1


def test_resolution_application_rejects_changed_plan_before_publication(f):
    class NoPublish:
        def __call__(self, claim):
            raise AssertionError('publication must not run for a mismatched plan')

    f.stage(status=503)
    f.attach()
    changed = CrossrefSourceAdapter().compile(
        replace(f.plan.definition, contact_email='other@example.invalid'),
    )
    command = ResolveClaimedCrossrefCapture(
        NoPublish(),
        f.store,
        source=CrossrefSourceAdapter(),
        clock=lambda: NOW,
    )

    with pytest.raises(CrossrefAttachmentError, match='resolution_request_mismatch'):
        command(changed, f.claim)
