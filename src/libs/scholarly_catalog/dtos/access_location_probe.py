from dataclasses import dataclass


@dataclass(frozen=True)
class AccessLocationProbe:
    requested_url: str
    final_url: str
    redirect_chain: tuple[str, ...]
    network_hops: tuple[tuple[str, tuple[str, ...]], ...]
    http_status: int
    content_type: str | None
    observed_identifier_namespace: str | None
    observed_identifier_value: str | None
