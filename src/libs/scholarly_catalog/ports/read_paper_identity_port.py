from typing import Protocol

from libs.scholarly_catalog.dtos.paper_identity_view import PaperIdentityView


class ReadPaperIdentityPort(Protocol):
    def __call__(self, namespace: str, value: str) -> PaperIdentityView: ...
