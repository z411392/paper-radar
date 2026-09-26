import hashlib
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from libs.delivery.dtos.delivery_subscription import (
    ConfigureDeliverySubscriptionRequest,
)
from libs.delivery.exceptions.delivery_subscription_error import (
    DeliverySubscriptionError,
)


class DeliverySubscriptionRules:
    @staticmethod
    def _text(value: object, code: str, maximum: int) -> str:
        if not isinstance(value, str):
            raise DeliverySubscriptionError(code)
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            raise DeliverySubscriptionError(code) from None
        if (
            not value
            or value != value.strip()
            or size > maximum
            or "\0" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise DeliverySubscriptionError(code)
        return value

    @classmethod
    def reader_id(cls, value: object) -> str:
        return cls._text(
            value,
            "invalid_delivery_reader",
            256,
        )

    @classmethod
    def normalize(
        cls,
        request: ConfigureDeliverySubscriptionRequest,
    ) -> tuple[str, str, str, int, str, bool]:
        if not isinstance(request, ConfigureDeliverySubscriptionRequest):
            raise DeliverySubscriptionError("invalid_delivery_subscription")
        reader_id = cls.reader_id(request.reader_id)
        timezone_name = cls._text(
            request.timezone,
            "invalid_delivery_timezone",
            128,
        )
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise DeliverySubscriptionError(
                "invalid_delivery_timezone"
            ) from None
        if (
            not isinstance(request.local_time, str)
            or re.fullmatch(
                r"(?:[01][0-9]|2[0-3]):[0-5][0-9]",
                request.local_time,
            )
            is None
        ):
            raise DeliverySubscriptionError("invalid_delivery_local_time")
        if (
            type(request.max_items) is not int
            or not 1 <= request.max_items <= 100
        ):
            raise DeliverySubscriptionError("invalid_delivery_max_items")
        recipient_ref = cls._text(
            request.recipient_ref,
            "invalid_delivery_recipient_ref",
            256,
        )
        if type(request.enabled) is not bool:
            raise DeliverySubscriptionError("invalid_delivery_enabled")
        schedule_json = json.dumps(
            {"kind": "daily", "local_time": request.local_time},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            reader_id,
            timezone_name,
            schedule_json,
            request.max_items,
            recipient_ref,
            request.enabled,
        )

    @staticmethod
    def subscription_id(reader_id: str) -> str:
        payload = ("delivery-subscription-v1\0" + reader_id + "\0email").encode(
            "utf-8"
        )
        return "subscription:" + hashlib.sha256(payload).hexdigest()

    @staticmethod
    def instant(value: object) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise DeliverySubscriptionError("invalid_delivery_subscription_time")
        try:
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise DeliverySubscriptionError(
                "invalid_delivery_subscription_time"
            ) from None
