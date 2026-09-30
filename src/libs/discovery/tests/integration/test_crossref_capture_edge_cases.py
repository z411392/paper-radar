"""Post-implementation adversarial receipts and HTTP cleanup regressions."""
import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from libs.discovery.adapters.driven.http_client_crossref_transport_adapter import HttpClientCrossrefTransportAdapter
from libs.discovery.domain.services.crossref_capture_rules import CrossrefCaptureRules
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError
from libs.discovery.tests.unit.test_crossref_http_capture import EMAIL, BODY, MemoryConnection, plan_request, response
from libs.discovery.tests.integration.test_crossref_capture_receipts import setup, response as captured_response


@pytest.mark.parametrize("resource", ["response", "connection"])
def test_close_failure_does_not_erase_completed_response(resource):
    class CloseFailsConnection(MemoryConnection):
        def getresponse(self):
            resp=super().getresponse()
            if resource=="response":
                original=resp.close
                def close():
                    original()
                    raise OSError("injected close error")
                resp.close=close
            return resp
        def close(self):
            self.closed=True
            if resource=="connection":
                raise OSError("injected close error")
    p,q=plan_request()
    conn=CloseFailsConnection(response())
    with patch("http.client.HTTPSConnection",return_value=conn):
        result=HttpClientCrossrefTransportAdapter(enabled=True,contact_email=EMAIL).get(p,q)
    assert result.complete and result.body==BODY
    assert conn.closed


def test_string_disguised_as_header_pair_is_rejected_on_readback(tmp_path):
    p,q,objects,store,*_=setup(tmp_path)
    rid=store.save(q,captured_response(),attempt_key="forged")
    envelope=json.loads(objects.read(rid))
    envelope["headers"]=["ab"]
    altered=objects.publish(json.dumps(envelope).encode(),"raw","application/octet-stream","source-response")
    with pytest.raises(CrossrefCaptureError,match="invalid_crossref_capture_receipt"):
        store.read(altered.object_id)


@pytest.mark.parametrize("change",[
    {"complete":1}, {"status":True}, {"body":"string"}, {"received_at":None},
    {"complete":False}, {"capture_error":"unexpected"},
    {"headers":(("Content-Length","0"),)},
])
def test_malformed_typed_capture_never_publishes_body(tmp_path,change):
    p,q,objects,store,*_=setup(tmp_path)
    with pytest.raises(CrossrefCaptureError):
        store.save(q,replace(captured_response(),**change),attempt_key="bad")
    assert objects.calls==[]


def test_request_and_headers_cannot_inject_unbounded_receipt(tmp_path):
    p,q,objects,store,*_=setup(tmp_path)
    with pytest.raises(CrossrefCaptureError):
        store.save(q,captured_response(headers=(("location","x"*4097),)),attempt_key="oversize")
    assert objects.calls==[]


def test_duplicate_content_encoding_is_not_guessed():
    with pytest.raises(CrossrefCaptureError,match="crossref_content_encoding_unsupported"):
        CrossrefCaptureRules.entity(captured_response(headers=(("content-encoding","gzip"),
                                                               ("content-encoding","identity"))))


def test_gate_exit_failure_returns_saved_receipt_reference(tmp_path):
    from contextlib import contextmanager
    from libs.discovery.application.commands.capture_crossref_page import CaptureCrossrefPage
    from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
    from libs.discovery.tests.integration.test_crossref_capture_receipts import Gate
    p,q,objects,store,_,transport,_=setup(tmp_path)
    class ExitFails(Gate):
        @contextmanager
        def slot(self,contact):
            yield self
            raise OSError("injected gate exit failure")
    command=CaptureCrossrefPage(transport,ExitFails(),store,source=CrossrefSourceAdapter(),enabled=True)
    with pytest.raises(CrossrefCaptureError,match="crossref_gate_finalize_failed") as err:
        command(p,q,attempt_key="exit-failed")
    assert store.read(err.value.receipt_id).capture.body
