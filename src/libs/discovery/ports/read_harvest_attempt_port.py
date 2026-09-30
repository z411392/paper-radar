from typing import Protocol

from libs.discovery.dtos.harvest_attempt import HarvestAttempt


class ReadHarvestAttemptPort(Protocol):
    def __call__(self, attempt_id: str) -> HarvestAttempt: ...
