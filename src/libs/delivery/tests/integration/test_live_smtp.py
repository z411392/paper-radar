import os

import pytest

from libs.delivery.adapters.driven.smtp_mail_sender_adapter import (
    SmtpMailSenderAdapter,
)
from libs.delivery.dtos.delivery_dispatch import MailMessage


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise AssertionError(f"missing live SMTP env: {name}")
    return value


@pytest.mark.live_external
@pytest.mark.skipif(
    os.environ.get("PAPER_RADAR_LIVE_SMTP") != "1",
    reason="set PAPER_RADAR_LIVE_SMTP=1 for the bounded SMTP smoke",
)
def test_live_smtp_sends_one_bounded_message() -> None:
    port_text = _required("PAPER_RADAR_SMTP_PORT")
    try:
        port = int(port_text)
    except ValueError:
        raise AssertionError("invalid live SMTP port") from None

    security = _required("PAPER_RADAR_SMTP_SECURITY")
    smoke_id = _required("PAPER_RADAR_SMTP_SMOKE_ID")

    sender = SmtpMailSenderAdapter(
        host=_required("PAPER_RADAR_SMTP_HOST"),
        port=port,
        sender=_required("PAPER_RADAR_SMTP_SENDER"),
        username=_required("PAPER_RADAR_SMTP_USERNAME"),
        password=_required("PAPER_RADAR_SMTP_PASSWORD"),
        security=security,
        timeout_seconds=30.0,
    )
    result = sender.send(
        MailMessage(
            recipient=_required("PAPER_RADAR_SMTP_RECIPIENT"),
            subject=f"[Paper Radar] SMTP live smoke {smoke_id}",
            text_body=(
                "Paper Radar live SMTP smoke. "
                "This message contains no paper or model content."
            ),
            html_body=(
                "<p>Paper Radar live SMTP smoke.</p>"
                "<p>This message contains no paper or model content.</p>"
            ),
            idempotency_key=f"live-smtp:{smoke_id}",
        )
    )

    assert result.state == "provider_accepted", (
        result.state,
        result.error_code,
    )
