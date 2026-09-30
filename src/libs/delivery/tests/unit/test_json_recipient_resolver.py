import pytest

from libs.delivery.adapters.driven.json_recipient_resolver_adapter import (
    JsonRecipientResolverAdapter,
)
from libs.delivery.exceptions.mail_configuration_error import (
    MailConfigurationError,
)


def test_recipient_map_resolves_exact_reference() -> None:
    resolver = JsonRecipientResolverAdapter(
        '{"recipient:primary":"reader@example.com",'
        '"recipient:backup":"b@example.com"}'
    )

    assert resolver.resolve("recipient:primary") == "reader@example.com"
    assert resolver.resolve("recipient:missing") is None


@pytest.mark.parametrize(
    "payload",
    [
        '{}',
        '{"recipient:primary":"reader@example.com",'
        '"recipient:primary":"other@example.com"}',
        '{"":"reader@example.com"}',
        '{"recipient:primary":"not-an-email"}',
        '{"recipient:primary":"bad @example.com"}',
        '[]',
    ],
)
def test_invalid_recipient_map_fails_closed(payload: str) -> None:
    with pytest.raises(MailConfigurationError, match="invalid_recipient_map"):
        JsonRecipientResolverAdapter(payload)
