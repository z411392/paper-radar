"""Author-owned HTTP admission policy oracles, not product/live acceptance."""

from datetime import datetime, timezone

import pytest

from libs.discovery.domain.services.crossref_rate_policy import CrossrefRatePolicy
from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)
POLICY = CrossrefRatePolicy()


def decide(status=200, headers=(), **kw):
    return POLICY.evaluate(status, headers, now=NOW, **kw)


def test_missing_limits_use_local_ceiling_not_legacy_single_record_rate():
    outcome = decide()
    assert outcome.action == "accept"
    assert outcome.minimum_interval_seconds == 1.0
    assert outcome.reported_concurrency is None
    assert "crossref_rate_headers_missing" in outcome.warnings


def test_runtime_slower_rate_and_open_type_are_preserved():
    outcome = decide(headers=(
        ("X-Rate-Limit-Limit", "2"), ("x-rate-limit-interval", "10s"),
        ("x-rate-limit-type", "future-provider-bucket"), ("x-concurrency-limit", "3"),
    ))
    assert outcome.minimum_interval_seconds == 5.0
    assert outcome.reported_concurrency == 3
    assert outcome.rate_type == "future-provider-bucket"
    assert outcome.warnings == ()


def test_advertised_faster_rate_never_increases_local_ceiling():
    outcome = decide(headers=(("x-rate-limit-limit", "10"), ("x-rate-limit-interval", "1s")))
    assert outcome.minimum_interval_seconds == 1.0


@pytest.mark.parametrize("headers", [
    (("x-rate-limit-limit", "0"), ("x-rate-limit-interval", "1s")),
    (("x-rate-limit-limit", "-1"), ("x-rate-limit-interval", "1s")),
    (("x-rate-limit-limit", "3.5"), ("x-rate-limit-interval", "1s")),
    (("x-rate-limit-limit", "3"), ("x-rate-limit-interval", "0s")),
    (("x-rate-limit-limit", "3"), ("x-rate-limit-interval", "NaNs")),
    (("x-rate-limit-limit", "3"), ("x-rate-limit-interval", "1ms")),
    (("x-rate-limit-limit", "3"),),
    (("x-rate-limit-limit", "3"), ("X-Rate-Limit-Limit", "10")),
])
def test_malformed_limits_are_visible_and_cannot_relax_budget(headers):
    outcome = decide(headers=headers)
    assert outcome.minimum_interval_seconds >= 1.0
    assert "crossref_rate_headers_invalid" in outcome.warnings


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "2.5", "999999999"])
def test_malformed_concurrency_is_never_an_admission_hint(value):
    outcome = decide(headers=(("x-concurrency-limit", value),))
    assert outcome.reported_concurrency is None
    assert "crossref_concurrency_header_invalid" in outcome.warnings


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 599])
def test_retry_without_retry_after_is_bounded_and_jittered(status):
    first = decide(status, failures=0, jitter=0)
    late = decide(status, failures=100, jitter=1)
    assert first.action == late.action == "retry"
    assert 1 <= first.delay_seconds <= 2
    assert late.delay_seconds == 300
    assert not first.open_circuit


def test_retry_after_is_lower_bound_not_clamped_to_local_backoff_cap():
    outcome = decide(429, (("Retry-After", "3600"),), failures=0, jitter=0)
    assert outcome.delay_seconds == 3600


def test_retry_after_http_date_accounts_for_server_clock_skew():
    outcome = decide(503, (
        ("Retry-After", "Thu, 24 Sep 2026 00:02:00 GMT"),
        ("Date", "Wed, 23 Sep 2026 23:59:00 GMT"),
    ))
    assert outcome.delay_seconds == 180


@pytest.mark.parametrize("headers", [
    (("Retry-After", "nonsense"),),
    (("Retry-After", "-1"),),
    (("Retry-After", "NaN"),),
    (("Retry-After", "1"), ("retry-after", "900")),
    (("Retry-After", "99999999999999999999"),),
])
def test_ambiguous_or_excessive_retry_after_requires_operator_not_early_retry(headers):
    outcome = decide(429, headers)
    assert outcome.action == "stop"
    assert outcome.open_circuit
    assert outcome.failure_code == "crossref_retry_after_invalid"


def test_403_wins_even_when_operational_headers_are_malformed():
    outcome = decide(403, (("Retry-After", "bad"), ("x-rate-limit-limit", "0")))
    assert outcome.action == "stop"
    assert outcome.open_circuit
    assert outcome.failure_code == "crossref_forbidden"


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 404, 422])
def test_redirect_or_permanent_error_never_retries_or_follows_url(status):
    outcome = decide(status, (("location", "https://publisher.invalid/paper"),))
    assert outcome.action == "stop"
    assert outcome.delay_seconds == 0


def test_truncated_200_is_not_acceptable_but_is_retryable():
    outcome = decide(200, capture_error="response_incomplete")
    assert outcome.action == "retry"
    assert outcome.failure_code == "response_incomplete"


def test_unknown_capture_error_does_not_become_success():
    outcome = decide(200, capture_error="unsupported_content_encoding")
    assert outcome.action == "stop"
    assert outcome.failure_code == "unsupported_content_encoding"


def test_network_timeout_without_response_is_retryable():
    outcome = decide(None, capture_error="source_timeout")
    assert outcome.action == "retry"


@pytest.mark.parametrize("args", [
    {"status": True}, {"status": 999}, {"headers": []},
    {"headers": (("bad\nname", "value"),)}, {"headers": (("date", "bad\rvalue"),)},
    {"failures": True}, {"failures": -1}, {"jitter": float("nan")}, {"jitter": 1.1},
    {"now": datetime(2026, 9, 24)},
])
def test_invalid_policy_inputs_fail_with_stable_diagnostics(args):
    kw = {"status": 200, "headers": (), "now": NOW}
    kw.update(args)
    with pytest.raises(CrossrefRateError):
        POLICY.evaluate(**kw)
