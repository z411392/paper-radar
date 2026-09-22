import http.client
import json

import pytest

from libs.paper_explanations.tests.live_support.openrouter_smoke import probe, self_test


def test_self_checks_never_use_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network forbidden in offline checks")

    monkeypatch.setattr(http.client, "HTTPSConnection", forbidden)
    self_test()


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_failed_authentication_cannot_start_generation(status: int) -> None:
    calls: list[str] = []

    def send(method: str, path: str, key: str, data: bytes | None = None):
        calls.append(method)
        return status, {"unsafe_echo": key}

    result = probe("FAKE_PRIVATE_SENTINEL", send)
    assert calls == ["GET"]
    assert result["completion_attempts"] == 0
    assert result["status"] == "FAIL"
    assert "FAKE_PRIVATE_SENTINEL" not in json.dumps(result)


def test_uncertain_generation_is_not_retried_or_echoed() -> None:
    calls: list[str] = []

    def send(method: str, path: str, key: str, data: bytes | None = None):
        calls.append(method)
        if method == "GET":
            return 200, {"data": {}}
        raise TimeoutError(key)

    result = probe("FAKE_PRIVATE_SENTINEL", send)
    assert calls == ["GET", "POST"]
    assert result["completion_attempts"] == 1
    assert result["status"] == "FAIL"
    assert "FAKE_PRIVATE_SENTINEL" not in json.dumps(result)
