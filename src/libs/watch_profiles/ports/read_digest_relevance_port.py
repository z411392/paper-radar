from typing import Protocol

from libs.watch_profiles.dtos.digest_relevance import DigestRelevance


class ReadDigestRelevancePort(Protocol):
    def __call__(
        self,
        reader_id: str,
        revision_id: str,
    ) -> tuple[DigestRelevance, ...]: ...
