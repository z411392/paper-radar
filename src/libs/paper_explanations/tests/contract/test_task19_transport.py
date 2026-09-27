from unittest.mock import Mock

import pytest

from libs.paper_explanations.adapters.driven.https_openrouter_transport import HttpsOpenRouterTransport
from libs.paper_explanations.adapters.driven.static_model_credential_adapter import (
    StaticModelCredentialAdapter,
)
from libs.paper_explanations.dtos.openrouter_policy import OpenRouterPolicy
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError

MODULE = 'libs.paper_explanations.adapters.driven.https_openrouter_transport'
SENTINEL = 'sk-or-v1-fake-not-a-real-secret-000000'


def setup_transport(monkeypatch, *, status=200, content=b'{"ok":true}', enabled=True):
    monkeypatch.setenv('OPENROUTER_API_KEY', SENTINEL)
    connection = Mock()
    response = connection.getresponse.return_value
    response.status = status
    response.getheader.return_value = None
    response.read.return_value = content
    constructor = Mock(return_value=connection)
    monkeypatch.setattr(MODULE + '.http.client.HTTPSConnection', constructor)
    policy = OpenRouterPolicy(768, '1', '5', enabled=enabled, max_response_bytes=1024)
    return HttpsOpenRouterTransport(policy), constructor, connection


def test_fixed_https_endpoint_one_call_and_connection_closed(monkeypatch):
    transport, constructor, connection = setup_transport(monkeypatch)
    response = transport.post(b'{"data":"fixture"}')
    assert response.status == 200
    assert constructor.call_count == 1
    assert constructor.call_args.args == ('openrouter.ai',)
    assert constructor.call_args.kwargs['port'] == 443
    assert connection.request.call_args.args == ('POST', '/api/v1/chat/completions')
    assert connection.request.call_args.kwargs['headers']['Authorization'] == 'Bearer ' + SENTINEL
    assert 'OPENROUTER' not in repr(transport)
    connection.getresponse.return_value.read.assert_called_once_with(1025)
    connection.close.assert_called_once()


def test_disabled_transport_does_not_read_credentials_or_create_connection(monkeypatch):
    transport, constructor, _ = setup_transport(monkeypatch, enabled=False)
    read_key = Mock(side_effect=AssertionError('must not read key'))
    monkeypatch.setattr(MODULE + '.os.environ.get', read_key)
    with pytest.raises(ModelGatewayError, match='model_disabled'):
        transport.post(b'{}')
    constructor.assert_not_called()
    read_key.assert_not_called()


@pytest.mark.parametrize('status', [301, 307, 400, 401, 402, 429, 500, 503])
def test_redirect_and_error_bodies_are_not_read_or_followed(monkeypatch, status):
    transport, constructor, connection = setup_transport(monkeypatch, status=status)
    result = transport.post(b'{}')
    assert result.status == status and result.body == b''
    assert constructor.call_count == 1 and connection.request.call_count == 1
    connection.getresponse.return_value.read.assert_not_called()
    connection.close.assert_called_once()


@pytest.mark.parametrize(('key', 'code'), [
    (None, 'credential_missing'), ('bad\r\nkey', 'credential_invalid'),
])
def test_missing_or_malformed_key_is_rejected_before_network(monkeypatch, key, code):
    transport, constructor, _ = setup_transport(monkeypatch)
    if key is None:
        monkeypatch.delenv('OPENROUTER_API_KEY')
    else:
        monkeypatch.setenv('OPENROUTER_API_KEY', key)
    with pytest.raises(ModelGatewayError, match=code):
        transport.post(b'{}')
    constructor.assert_not_called()


@pytest.mark.parametrize(('failure', 'code'), [
    (TimeoutError(SENTINEL), 'timeout'), (OSError(SENTINEL), 'transport_error'),
])
def test_network_failure_is_sanitized_and_never_retried(monkeypatch, failure, code):
    transport, constructor, connection = setup_transport(monkeypatch)
    connection.getresponse.side_effect = failure
    with pytest.raises(ModelGatewayError, match=code) as raised:
        transport.post(b'{}')
    assert SENTINEL not in str(raised.value)
    assert raised.value.__suppress_context__ is True
    assert constructor.call_count == 1 and connection.request.call_count == 1
    connection.close.assert_called_once()


def test_response_limit_is_enforced(monkeypatch):
    transport, _, connection = setup_transport(monkeypatch, content=b'x' * 1025)
    with pytest.raises(ModelGatewayError, match='response_too_large'):
        transport.post(b'{}')
    connection.close.assert_called_once()


def test_credential_echo_is_rejected_before_it_reaches_receipts(monkeypatch):
    transport, _, connection = setup_transport(monkeypatch, content=('{"echo":"' + SENTINEL + '"}').encode())
    with pytest.raises(ModelGatewayError, match='credential_echo_rejected'):
        transport.post(b'{}')
    connection.close.assert_called_once()


def test_short_body_with_declared_content_length_is_not_a_complete_response(monkeypatch):
    transport, _, connection = setup_transport(monkeypatch, content=b'{}')
    connection.getresponse.return_value.getheader.side_effect = (
        lambda name: '20' if name == 'Content-Length' else None
    )
    with pytest.raises(ModelGatewayError, match='incomplete_http_response'):
        transport.post(b'{}')


def test_json_escaped_credential_echo_is_rejected(monkeypatch):
    body = ('{"content":"' + SENTINEL.replace('s', '\\u0073', 1) + '"}').encode()
    transport, _, _ = setup_transport(monkeypatch, content=body)
    with pytest.raises(ModelGatewayError, match='credential_echo_rejected'):
        transport.post(b'{}')



def test_injected_credential_bypasses_process_environment(monkeypatch):
    read_env = Mock(side_effect=AssertionError("must not read process environment"))
    monkeypatch.setattr(MODULE + ".os.environ.get", read_env)
    connection = Mock()
    response = connection.getresponse.return_value
    response.status = 200
    response.getheader.return_value = None
    response.read.return_value = b'{"ok":true}'
    constructor = Mock(return_value=connection)
    monkeypatch.setattr(MODULE + ".http.client.HTTPSConnection", constructor)
    credential = Mock(return_value=SENTINEL)
    policy = OpenRouterPolicy(768, "1", "5", enabled=True, max_response_bytes=1024)
    transport = HttpsOpenRouterTransport(policy, credential)

    result = transport.post(b"{}")

    assert result.status == 200
    credential.assert_called_once_with()
    read_env.assert_not_called()
    assert connection.request.call_args.kwargs["headers"]["Authorization"] == (
        "Bearer " + SENTINEL
    )


def test_disabled_injected_transport_does_not_read_credential():
    credential = Mock(side_effect=AssertionError("must not read credential"))
    policy = OpenRouterPolicy(768, "1", "5", enabled=False)
    transport = HttpsOpenRouterTransport(policy, credential)

    with pytest.raises(ModelGatewayError, match="model_disabled"):
        transport.post(b"{}")

    credential.assert_not_called()


def test_static_credential_validation_never_exposes_secret_in_repr():
    credential = StaticModelCredentialAdapter(SENTINEL)

    assert credential() == SENTINEL
    assert SENTINEL not in repr(credential)
    with pytest.raises(ModelGatewayError, match="credential_invalid"):
        StaticModelCredentialAdapter("short")
