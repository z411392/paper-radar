"""Receipt orchestration via real on-disk port test double, not full kernel acceptance."""
import gzip
import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.adapters.driven.kernel_crossref_capture_store_adapter import KernelCrossrefCaptureStoreAdapter
from libs.discovery.application.commands.capture_crossref_page import CaptureCrossrefPage
from libs.discovery.application.queries.replay_crossref_capture import ReplayCrossrefCapture
from libs.discovery.domain.services.crossref_rate_policy import CrossrefRatePolicy
from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError

NOW = datetime(2026,9,24,tzinfo=timezone.utc)
EMAIL = "paper@example.org"
BODY = b'{"status":"ok","message-type":"work-list","message":{"items":[],"total-results":0}}'


def request():
    source = CrossrefSourceAdapter()
    plan = source.compile(CrossrefWindowInput("b", "statistics", NOW.replace(day=23), NOW, EMAIL, "v1", 2))
    return plan, source.page(plan)


class DiskObjects:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.fail_at = 0
    def publish(self, content, kind, media_type, retention):
        self.calls.append((content,kind,media_type,retention))
        if self.fail_at == len(self.calls):
            raise OSError("injected write failure")
        digest=hashlib.sha256(content).hexdigest()
        p=self.root/digest
        if p.exists():
            assert p.read_bytes()==content
        else:
            with p.open("xb") as f:
                f.write(content)
        return SimpleNamespace(object_id="raw:"+digest,state="available",content_sha256=digest,byte_size=len(content))
    def read(self, object_id):
        return (self.root/object_id.split(":",1)[1]).read_bytes()


class Gate:
    def __init__(self):
        self.calls=[]
        self.observed=[]
        self.fail=False
    @contextmanager
    def slot(self, contact):
        self.calls.append(contact)
        yield self
    def observe(self,status,headers,*,capture_error=None):
        self.observed.append((status,headers,capture_error))
        if self.fail:
            raise RuntimeError("injected gate failure")
        return CrossrefRatePolicy().evaluate(status,headers,now=NOW,capture_error=capture_error)


def response(body=BODY,status=200,complete=True,error=None,headers=()):
    return CrossrefHttpCapture(status,tuple(headers),body,NOW,complete,error)


def setup(tmp_path, captured=None):
    p,q=request()
    objects=DiskObjects(tmp_path)
    store=KernelCrossrefCaptureStoreAdapter(objects.publish,objects.read)
    gate=Gate()
    transport=Mock()
    transport.get.return_value=captured or response()
    command=CaptureCrossrefPage(transport,gate,store,source=CrossrefSourceAdapter(),enabled=True)
    return p,q,objects,store,gate,transport,command


def test_save_then_reopen_and_replay_without_network(tmp_path):
    coded=gzip.compress(BODY)
    p,q,objects,store,gate,transport,command=setup(tmp_path,response(coded,headers=(("content-encoding","gzip"),)))
    result=command(p,q,attempt_key="attempt:1")
    assert result.decision.action=="accept"
    assert len(objects.calls)==2
    assert objects.calls[0][0]==coded
    assert all(call[1:]==("raw","application/octet-stream","source-response") for call in objects.calls)
    receipt=json.loads(objects.calls[1][0])
    assert receipt["hash_scope"]=="http_content_coded_body"
    assert receipt["body_sha256"]==hashlib.sha256(coded).hexdigest()
    reopened=KernelCrossrefCaptureStoreAdapter(objects.publish,DiskObjects(tmp_path).read)
    replay=ReplayCrossrefCapture(reopened,CrossrefSourceAdapter())(p,q,result.receipt_id)
    assert replay.content_coded_sha256==hashlib.sha256(coded).hexdigest()
    assert replay.entity_sha256==hashlib.sha256(BODY).hexdigest()
    assert replay.page.traversal_end_hint
    transport.get.assert_called_once()
    assert gate.calls==[EMAIL]


@pytest.mark.parametrize("status",[301,308,403,429,500,503])
def test_failure_response_is_persisted_but_never_decoded(tmp_path,status):
    p,q,objects,store,gate,transport,command=setup(tmp_path,response(b"error",status))
    result=command(p,q,attempt_key="attempt:failure")
    saved=store.read(result.receipt_id)
    assert saved.capture.status==status and saved.capture.body==b"error"
    decoder=Mock()
    with pytest.raises(CrossrefCaptureError,match="crossref_capture_not_parseable"):
        ReplayCrossrefCapture(store,decoder)(p,q,result.receipt_id)
    decoder.decode.assert_not_called()
    assert len(gate.observed)==1


def test_partial_body_is_durable_prefix_not_full_metadata(tmp_path):
    p,q,objects,store,gate,transport,command=setup(tmp_path,response(b"partial",complete=False,error="response_incomplete"))
    result=command(p,q,attempt_key="partial")
    receipt=json.loads(objects.read(result.receipt_id))
    assert receipt["hash_scope"]=="http_content_coded_prefix"
    assert result.decision.action=="retry"
    with pytest.raises(CrossrefCaptureError):
        ReplayCrossrefCapture(store,CrossrefSourceAdapter())(p,q,result.receipt_id)


@pytest.mark.parametrize("fail_at",[1,2])
def test_storage_failure_never_reaches_decoder_but_gate_still_observes(tmp_path,fail_at):
    p,q,objects,store,gate,transport,command=setup(tmp_path,response(b"blocked",403))
    objects.fail_at=fail_at
    with pytest.raises(OSError,match="injected"):
        command(p,q,attempt_key="failed")
    assert gate.observed[0][0]==403
    assert len(list(tmp_path.iterdir()))==fail_at-1


def test_gate_failure_does_not_erase_receipt(tmp_path):
    p,q,objects,store,gate,transport,command=setup(tmp_path)
    gate.fail=True
    with pytest.raises(CrossrefCaptureError,match="crossref_gate_observation_failed") as err:
        command(p,q,attempt_key="gate-failed")
    assert err.value.receipt_id is not None
    assert store.read(err.value.receipt_id).capture.body==BODY


def test_default_disabled_does_not_touch_transport_gate_or_store(tmp_path):
    p,q,objects,store,gate,transport,_=setup(tmp_path)
    with pytest.raises(CrossrefCaptureError,match="crossref_capture_disabled"):
        CaptureCrossrefPage(transport,gate,store,source=CrossrefSourceAdapter())(p,q,attempt_key="disabled")
    transport.get.assert_not_called()
    assert objects.calls==[] and gate.calls==[]


def test_same_receipt_replay_is_deterministic_but_distinct_attempt_is_preserved(tmp_path):
    p,q,objects,store,*_=setup(tmp_path)
    first=store.save(q,response(),attempt_key="a")
    second=store.save(q,response(),attempt_key="a")
    third=store.save(q,response(),attempt_key="b")
    assert first==second and third!=first
    assert len(list(tmp_path.iterdir()))==3


def test_body_corruption_is_detected_after_reopen(tmp_path):
    p,q,objects,store,*_=setup(tmp_path)
    rid=store.save(q,response(),attempt_key="a")
    body_id=store.read(rid).body_object_id
    (tmp_path/body_id.split(":")[1]).write_bytes(b"changed")
    with pytest.raises(CrossrefCaptureError,match="crossref_capture_body_mismatch"):
        store.read(rid)


def test_receipt_corruption_is_detected(tmp_path):
    p,q,objects,store,*_=setup(tmp_path)
    rid=store.save(q,response(),attempt_key="a")
    (tmp_path/rid.split(":")[1]).write_bytes(b"{}")
    with pytest.raises(CrossrefCaptureError,match="crossref_capture_receipt_mismatch"):
        store.read(rid)


def test_receipt_from_different_window_cannot_be_replayed(tmp_path):
    p,q,objects,store,*_=setup(tmp_path)
    rid=store.save(q,response(),attempt_key="a")
    source=CrossrefSourceAdapter()
    other=source.compile(replace(p.definition,binding_key="other"))
    with pytest.raises(CrossrefCaptureError,match="crossref_capture_request_mismatch"):
        ReplayCrossrefCapture(store,source)(other,source.page(other),rid)


def test_invalid_json_is_kept_as_raw_evidence(tmp_path):
    p,q,objects,store,gate,transport,command=setup(tmp_path,response(b"{broken"))
    result=command(p,q,attempt_key="badjson")
    from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError
    with pytest.raises(CrossrefProtocolError):
        ReplayCrossrefCapture(store,CrossrefSourceAdapter())(p,q,result.receipt_id)
    assert store.read(result.receipt_id).capture.body==b"{broken"
