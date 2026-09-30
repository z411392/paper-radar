from datetime import datetime, timezone


class SystemWorkflowClockAdapter:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
