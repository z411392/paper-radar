import time
from datetime import datetime, timezone
from uuid import uuid4


class SystemHarvestRuntimeAdapter:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def new_attempt_id(self) -> str:
        return "attempt:" + uuid4().hex

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
