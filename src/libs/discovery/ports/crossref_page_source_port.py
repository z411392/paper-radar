from typing import Protocol

from libs.discovery.dtos.crossref_page import (
    CrossrefDecodedPage,
    CrossrefPageRequest,
    CrossrefWindowInput,
    CrossrefWindowPlan,
)


class CrossrefPageSourcePort(Protocol):
    def compile(self, definition: CrossrefWindowInput) -> CrossrefWindowPlan: ...

    def page(self, plan: CrossrefWindowPlan, cursor: str = "*") -> CrossrefPageRequest: ...

    def decode(
        self,
        plan: CrossrefWindowPlan,
        request: CrossrefPageRequest,
        body: bytes,
        *,
        expected_sha256: str,
        http_status: int = 200,
    ) -> CrossrefDecodedPage: ...
