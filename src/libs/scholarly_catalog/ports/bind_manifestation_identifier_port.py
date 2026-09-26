from typing import Protocol


class BindManifestationIdentifierPort(Protocol):
    def __call__(
        self,
        *,
        manifestation_id: str,
        namespace: str,
        identifier_value: str,
        source_evidence_id: str,
    ) -> None: ...
