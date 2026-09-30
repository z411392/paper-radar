from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigurationDocument:
    kind: str
    canonical_json: str
    fingerprint: str
