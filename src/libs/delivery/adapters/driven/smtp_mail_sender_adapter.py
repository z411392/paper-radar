import hashlib
import math
import smtplib
import ssl
from email.message import EmailMessage

from libs.delivery.dtos.delivery_dispatch import MailMessage, MailSendResult
from libs.delivery.exceptions.mail_configuration_error import MailConfigurationError


class SmtpMailSenderAdapter:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        username: str,
        password: str,
        timeout_seconds: float = 20.0,
    ) -> None:
        if (
            not isinstance(host, str)
            or not host
            or host != host.strip()
            or len(host) > 253
            or "://" in host
            or any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in host)
            or type(port) is not int
            or not 1 <= port <= 65535
            or not isinstance(username, str)
            or not username
            or len(username) > 320
            or "\r" in username
            or "\n" in username
            or "\0" in username
            or not isinstance(password, str)
            or not password
            or len(password.encode("utf-8")) > 8192
            or "\r" in password
            or "\n" in password
            or "\0" in password
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 120
        ):
            raise MailConfigurationError("invalid_smtp_configuration")
        try:
            self._safe_address(sender)
        except ValueError as exc:
            raise MailConfigurationError("invalid_smtp_configuration") from exc
        self._host = host
        self._port = port
        self._sender = sender
        self._username = username
        self._password = password
        self._timeout = float(timeout_seconds)

    @staticmethod
    def _safe_address(value: object) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or value.count("@") != 1
            or any(ch.isspace() for ch in value)
            or "\r" in value
            or "\n" in value
        ):
            raise ValueError("invalid_email_address")
        return value

    def send(self, message: MailMessage) -> MailSendResult:
        if not isinstance(message, MailMessage):
            raise ValueError("invalid_mail_message")
        recipient = self._safe_address(message.recipient)
        sender = self._safe_address(self._sender)
        email = EmailMessage()
        email["From"] = sender
        email["To"] = recipient
        email["Subject"] = message.subject
        token = hashlib.sha256(message.idempotency_key.encode("utf-8")).hexdigest()
        email["Message-ID"] = f"<{token}@paper-radar.local>"
        email.set_content(message.text_body)
        email.add_alternative(message.html_body, subtype="html")

        try:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                self._host,
                self._port,
                timeout=self._timeout,
                context=context,
            ) as client:
                if self._username:
                    client.login(self._username, self._password)
                refused = client.send_message(email)
            if refused:
                return MailSendResult("rejected", None, "recipient_rejected")
            return MailSendResult("provider_accepted", None, None)
        except (
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
            smtplib.SMTPDataError,
            smtplib.SMTPAuthenticationError,
        ) as exc:
            return MailSendResult("rejected", None, exc.__class__.__name__)
        except (TimeoutError, OSError, smtplib.SMTPException) as exc:
            return MailSendResult("unknown", None, exc.__class__.__name__)
