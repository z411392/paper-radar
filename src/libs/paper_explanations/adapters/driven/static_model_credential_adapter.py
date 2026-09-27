import re

from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError


class StaticModelCredentialAdapter:
    """Keep one already-authorized credential in memory without exposing it in repr."""

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or re.fullmatch(
            r"[A-Za-z0-9_-]{16,512}",
            value,
        ) is None:
            raise ModelGatewayError("credential_invalid")
        self._value = value

    def __call__(self) -> str:
        return self._value
