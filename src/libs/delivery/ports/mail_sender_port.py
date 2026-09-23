from typing import Protocol

from libs.delivery.dtos.delivery_dispatch import MailMessage, MailSendResult


class MailSenderPort(Protocol):
    def send(self, message: MailMessage) -> MailSendResult: ...
