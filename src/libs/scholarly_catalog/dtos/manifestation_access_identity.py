from dataclasses import dataclass


@dataclass(frozen=True)
class ManifestationAccessIdentity:
    manifestation_id: str
    source_namespace: str
    native_id: str
