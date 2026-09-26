import pytest

from apps.http.security import LocalHttpBoundaryError, LocalHttpSecurityPolicy


TOKEN = "t" * 32


def test_ipv4_loopback_read_requires_exact_startup_host() -> None:
    policy = LocalHttpSecurityPolicy("127.0.0.1", 8765, TOKEN)

    policy.validate_read("127.0.0.1:8765")

    with pytest.raises(LocalHttpBoundaryError, match="invalid_local_host"):
        policy.validate_read("localhost:8765")
    with pytest.raises(LocalHttpBoundaryError, match="invalid_local_host"):
        policy.validate_read("127.0.0.1:9999")


def test_ipv6_loopback_has_bracketed_host_and_origin() -> None:
    policy = LocalHttpSecurityPolicy("::1", 8765, TOKEN)

    assert policy.host == "[::1]:8765"
    assert policy.origin == "http://[::1]:8765"
    policy.validate_read("[::1]:8765")


@pytest.mark.parametrize("bind_host", ["0.0.0.0", "::", "192.168.1.10", "localhost"])
def test_non_literal_loopback_bind_is_rejected(bind_host: str) -> None:
    with pytest.raises(LocalHttpBoundaryError, match="non_loopback_bind"):
        LocalHttpSecurityPolicy(bind_host, 8765, TOKEN)


@pytest.mark.parametrize("port", [0, -1, 65536, True])
def test_invalid_port_is_rejected(port: object) -> None:
    with pytest.raises(LocalHttpBoundaryError, match="invalid_local_port"):
        LocalHttpSecurityPolicy("127.0.0.1", port, TOKEN)  # type: ignore[arg-type]


def test_mutation_requires_exact_host_origin_and_capability() -> None:
    policy = LocalHttpSecurityPolicy("127.0.0.1", 8765, TOKEN)

    policy.validate_mutation(
        host="127.0.0.1:8765",
        origin="http://127.0.0.1:8765",
        mutation_token=TOKEN,
    )

    with pytest.raises(LocalHttpBoundaryError, match="invalid_local_host"):
        policy.validate_mutation(
            host="evil.test",
            origin="http://127.0.0.1:8765",
            mutation_token=TOKEN,
        )
    with pytest.raises(LocalHttpBoundaryError, match="invalid_mutation_origin"):
        policy.validate_mutation(
            host="127.0.0.1:8765",
            origin="https://evil.test",
            mutation_token=TOKEN,
        )
    with pytest.raises(LocalHttpBoundaryError, match="invalid_mutation_token"):
        policy.validate_mutation(
            host="127.0.0.1:8765",
            origin="http://127.0.0.1:8765",
            mutation_token="x" * 32,
        )


@pytest.mark.parametrize("token", ["", "short", "x" * 31])
def test_weak_startup_mutation_capability_is_rejected(token: str) -> None:
    with pytest.raises(LocalHttpBoundaryError, match="invalid_mutation_token"):
        LocalHttpSecurityPolicy("127.0.0.1", 8765, token)


def test_read_does_not_require_mutation_capability_from_request() -> None:
    policy = LocalHttpSecurityPolicy("127.0.0.1", 8765, TOKEN)

    policy.validate_read("127.0.0.1:8765")
