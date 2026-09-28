import pytest

from libs.delivery.adapters.driven.smtp_mail_sender_adapter import (
    SmtpMailSenderAdapter,
)
from libs.delivery.dtos.delivery_dispatch import MailMessage
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
        "security": "ssl",
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
        {"security": "plain"},
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


def test_starttls_smtp_configuration_is_valid() -> None:
    assert (
        SmtpMailSenderAdapter(
            **valid(
                port=587,
                security="starttls",
            )
        )
        is not None
    )


def test_starttls_send_upgrades_transport_before_authentication(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class FakeSmtp:
        def __init__(self, host: str, port: int, *, timeout: float) -> None:
            assert (host, port, timeout) == ("smtp.example.com", 587, 20.0)
            calls.append("connect")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def ehlo(self) -> None:
            calls.append("ehlo")

        def starttls(self, *, context) -> None:
            assert context is not None
            calls.append("starttls")

        def login(self, username: str, password: str) -> None:
            assert (username, password) == (
                "mailer@example.com",
                "secret value",
            )
            calls.append("login")

        def send_message(self, message) -> dict:
            assert message["To"] == "reader@example.com"
            calls.append("send")
            return {}

    def reject_ssl(*args, **kwargs):
        raise AssertionError("STARTTLS mode must not use SMTP_SSL")

    monkeypatch.setattr(
        "libs.delivery.adapters.driven.smtp_mail_sender_adapter.smtplib.SMTP",
        FakeSmtp,
    )
    monkeypatch.setattr(
        "libs.delivery.adapters.driven.smtp_mail_sender_adapter.smtplib.SMTP_SSL",
        reject_ssl,
    )

    sender = SmtpMailSenderAdapter(
        **valid(
            port=587,
            security="starttls",
        )
    )
    result = sender.send(
        MailMessage(
            recipient="reader@example.com",
            subject="Paper Radar",
            text_body="text",
            html_body="<p>text</p>",
            idempotency_key="digest:test",
        )
    )

    assert result.state == "provider_accepted"
    assert calls == [
        "connect",
        "ehlo",
        "starttls",
        "ehlo",
        "login",
        "send",
    ]


@pytest.mark.parametrize(
    "error",
    [
        __import__("smtplib").SMTPDataError(451, b"try later"),
        __import__("smtplib").SMTPSenderRefused(
            450,
            b"mailbox busy",
            "paper-radar@example.com",
        ),
        __import__("smtplib").SMTPAuthenticationError(
            454,
            b"temporary auth failure",
        ),
    ],
)
def test_smtp_4xx_response_is_retryable(
    monkeypatch,
    error,
) -> None:
    class FakeSmtp:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def login(self, username: str, password: str) -> None:
            if isinstance(error, __import__("smtplib").SMTPAuthenticationError):
                raise error

        def send_message(self, message):
            raise error

    monkeypatch.setattr(
        "libs.delivery.adapters.driven.smtp_mail_sender_adapter.smtplib.SMTP_SSL",
        FakeSmtp,
    )

    result = SmtpMailSenderAdapter(**valid()).send(
        MailMessage(
            recipient="reader@example.com",
            subject="Paper Radar",
            text_body="text",
            html_body="<p>text</p>",
            idempotency_key="digest:retryable",
        )
    )

    assert result.state == "retryable"


def test_smtp_recipient_4xx_is_retryable(monkeypatch) -> None:
    error = __import__("smtplib").SMTPRecipientsRefused(
        {"reader@example.com": (450, b"mailbox busy")}
    )

    class FakeSmtp:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def login(self, username: str, password: str) -> None:
            pass

        def send_message(self, message):
            raise error

    monkeypatch.setattr(
        "libs.delivery.adapters.driven.smtp_mail_sender_adapter.smtplib.SMTP_SSL",
        FakeSmtp,
    )

    result = SmtpMailSenderAdapter(**valid()).send(
        MailMessage(
            recipient="reader@example.com",
            subject="Paper Radar",
            text_body="text",
            html_body="<p>text</p>",
            idempotency_key="digest:recipient-retryable",
        )
    )

    assert result.state == "retryable"


@pytest.mark.parametrize(
    "error",
    [
        __import__("smtplib").SMTPDataError(550, b"rejected"),
        __import__("smtplib").SMTPAuthenticationError(
            535,
            b"bad credentials",
        ),
    ],
)
def test_smtp_5xx_response_is_terminal_rejection(
    monkeypatch,
    error,
) -> None:
    class FakeSmtp:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def login(self, username: str, password: str) -> None:
            if isinstance(error, __import__("smtplib").SMTPAuthenticationError):
                raise error

        def send_message(self, message):
            raise error

    monkeypatch.setattr(
        "libs.delivery.adapters.driven.smtp_mail_sender_adapter.smtplib.SMTP_SSL",
        FakeSmtp,
    )

    result = SmtpMailSenderAdapter(**valid()).send(
        MailMessage(
            recipient="reader@example.com",
            subject="Paper Radar",
            text_body="text",
            html_body="<p>text</p>",
            idempotency_key="digest:terminal",
        )
    )

    assert result.state == "rejected"


@pytest.mark.parametrize(
    "code,expected",
    [
        (450, "retryable"),
        (550, "rejected"),
    ],
)
def test_smtp_refused_result_uses_response_class(
    monkeypatch,
    code: int,
    expected: str,
) -> None:
    class FakeSmtp:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def login(self, username: str, password: str) -> None:
            pass

        def send_message(self, message):
            return {
                "reader@example.com": (
                    code,
                    b"temporary" if code < 500 else b"permanent",
                )
            }

    monkeypatch.setattr(
        "libs.delivery.adapters.driven.smtp_mail_sender_adapter.smtplib.SMTP_SSL",
        FakeSmtp,
    )

    result = SmtpMailSenderAdapter(**valid()).send(
        MailMessage(
            recipient="reader@example.com",
            subject="Paper Radar",
            text_body="text",
            html_body="<p>text</p>",
            idempotency_key="digest:refused-return",
        )
    )

    assert result.state == expected
