import os
from uuid import uuid4

import pytest

from libs.delivery.adapters.driven.smtp_mail_sender_adapter import (
    SmtpMailSenderAdapter,
)
from libs.delivery.dtos.delivery_dispatch import MailMessage


pytestmark = pytest.mark.live_external


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not isinstance(value, str) or not value:
        pytest.fail(f"missing required live SMTP setting: {name}")
    return value


@pytest.mark.skipif(
    os.environ.get("PAPER_RADAR_LIVE_SMTP") != "1",
    reason="set PAPER_RADAR_LIVE_SMTP=1 for the bounded live SMTP smoke",
)
def test_live_smtp_sends_one_fixed_message() -> None:
    security = _required("PAPER_RADAR_LIVE_SMTP_SECURITY")
    port_text = _required("PAPER_RADAR_LIVE_SMTP_PORT")
    if security not in {"ssl", "starttls"}:
        pytest.fail("PAPER_RADAR_LIVE_SMTP_SECURITY must be ssl or starttls")
    if not port_text.isascii() or not port_text.isdigit():
        pytest.fail("PAPER_RADAR_LIVE_SMTP_PORT must be an integer")
    port = int(port_text)
    expected_port = 465 if security == "ssl" else 587
    if port != expected_port:
        pytest.fail(
            "live SMTP smoke only permits ssl/465 or starttls/587"
        )

    sender = SmtpMailSenderAdapter(
        host=_required("PAPER_RADAR_LIVE_SMTP_HOST"),
        port=port,
        sender=_required("PAPER_RADAR_LIVE_SMTP_SENDER"),
        username=_required("PAPER_RADAR_LIVE_SMTP_USERNAME"),
        password=_required("PAPER_RADAR_LIVE_SMTP_PASSWORD"),
        security=security,
        timeout_seconds=20.0,
    )
    result = sender.send(
        MailMessage(
            recipient=_required("PAPER_RADAR_LIVE_SMTP_RECIPIENT"),
            subject="Paper Radar SMTP smoke",
            text_body=(
                "Paper Radar live SMTP smoke succeeded far enough to submit "
                "this single fixed test message."
            ),
            html_body=(
                "<p>Paper Radar live SMTP smoke succeeded far enough to "
                "submit this single fixed test message.</p>"
            ),
            idempotency_key=(
                os.environ.get("PAPER_RADAR_LIVE_SMTP_IDEMPOTENCY_KEY")
                or "smtp-smoke:" + uuid4().hex
            ),
        )
    )

    assert result.state == "provider_accepted", (
        "live SMTP smoke was not provider-accepted; "
        f"state={result.state}, error={result.error_code}"
    )
