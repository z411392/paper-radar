import pytest

from libs.delivery.adapters.driven.smtp_mail_sender_adapter import (
    SmtpMailSenderAdapter,
)
from libs.delivery.exceptions.mail_configuration_error import (
    MailConfigurationError,
)


def valid(**changes):
    values = {
        "host": "smtp.example.com",
        "port": 465,
        "sender": "paper-radar@example.com",
        "username": "mailer@example.com",
        "password": "secret value",
        "timeout_seconds": 20.0,
    }
    values.update(changes)
    return values


def test_valid_smtp_configuration_only_constructs_adapter() -> None:
    assert SmtpMailSenderAdapter(**valid()) is not None


@pytest.mark.parametrize(
    "changes",
    [
        {"host": ""},
        {"host": "https://smtp.example.com"},
        {"host": "smtp example.com"},
        {"port": 0},
        {"port": 65536},
        {"port": True},
        {"sender": "not-an-email"},
        {"username": ""},
        {"username": "bad\nuser"},
        {"password": ""},
        {"password": "bad\nsecret"},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": True},
    ],
)
def test_invalid_smtp_configuration_fails_before_any_network(
    changes: dict[str, object],
) -> None:
    with pytest.raises(
        MailConfigurationError,
        match="invalid_smtp_configuration",
    ):
        SmtpMailSenderAdapter(**valid(**changes))
