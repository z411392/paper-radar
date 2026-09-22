from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileRevision:
    profile_id: str
    revision: int
    current_revision: int
    fingerprint: str
    lifecycle: str
    scope_text: str
    filters_json: str
    domains: tuple[tuple[str, int], ...]
