from typing import Protocol


class RecipientResolverPort(Protocol):
    def resolve(self, recipient_ref: str) -> str | None: ...
