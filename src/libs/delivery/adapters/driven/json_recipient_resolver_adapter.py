import json

from libs.delivery.exceptions.mail_configuration_error import MailConfigurationError


class JsonRecipientResolverAdapter:
    def __init__(self, content: str) -> None:
        if (
            not isinstance(content, str)
            or not content
            or len(content.encode("utf-8")) > 1_000_000
        ):
            raise MailConfigurationError("invalid_recipient_map")

        def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        try:
            data = json.loads(
                content,
                object_pairs_hook=pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(
                    ValueError("nonfinite")
                ),
            )
        except (
            json.JSONDecodeError,
            ValueError,
            TypeError,
            UnicodeError,
            RecursionError,
        ):
            raise MailConfigurationError("invalid_recipient_map") from None
        if not isinstance(data, dict) or not 1 <= len(data) <= 1000:
            raise MailConfigurationError("invalid_recipient_map")

        values: dict[str, str] = {}
        for key, value in data.items():
            if (
                not isinstance(key, str)
                or not key.strip()
                or key != key.strip()
                or len(key.encode("utf-8")) > 256
                or "\0" in key
                or any(ord(char) < 32 or ord(char) == 127 for char in key)
                or not isinstance(value, str)
                or not self._valid_email(value)
            ):
                raise MailConfigurationError("invalid_recipient_map")
            values[key] = value
        self._values = values

    @staticmethod
    def _valid_email(value: str) -> bool:
        return (
            bool(value)
            and value == value.strip()
            and value.count("@") == 1
            and not value.startswith("@")
            and not value.endswith("@")
            and len(value) <= 254
            and "\r" not in value
            and "\n" not in value
            and "\0" not in value
            and not any(char.isspace() for char in value)
        )

    def resolve(self, recipient_ref: str) -> str | None:
        if not isinstance(recipient_ref, str):
            return None
        return self._values.get(recipient_ref)
