from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedIdentifier:
    namespace: str
    normalized_value: str
    native_version: str | None
