import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from libs.discovery.exceptions.source_fetch_error import SourceFetchError

_HTTP_DATE = re.compile(
    r"(?:[A-Za-z]{3}, [0-9]{2} [A-Za-z]{3} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT"
    r"|[A-Za-z]+, [0-9]{2}-[A-Za-z]{3}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT"
    r"|[A-Za-z]{3} [A-Za-z]{3} (?: [0-9]|[0-9]{2}) [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4})"
)


def parse_retry_after(value: str, received_at: datetime) -> float:
    """RFC 9110 delay-seconds and the three HTTP-date forms; no duration cap."""
    try:
        if not isinstance(value, str) or len(value) > 256 or "\r" in value or "\n" in value:
            raise ValueError
        value = value.strip(" \t")
        if not isinstance(received_at, datetime) or received_at.utcoffset() is None:
            raise ValueError
        if re.fullmatch(r"[0-9]+", value):
            delay = float(value)
        else:
            if not _HTTP_DATE.fullmatch(value):
                raise ValueError
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            delay = max(0.0, (target - received_at).total_seconds())
        if not math.isfinite(delay) or delay < 0:
            raise ValueError
        return max(3.0, delay)
    except (TypeError, ValueError, OverflowError, AttributeError) as exc:
        raise SourceFetchError("invalid_retry_after") from exc
