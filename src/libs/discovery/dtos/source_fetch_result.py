from dataclasses import dataclass

from libs.discovery.dtos.source_http_response import SourceHttpResponse


@dataclass(frozen=True)
class SourceFetchResult:
    request_fingerprint: str
    response: SourceHttpResponse | None
    response_sha256: str | None
    failure_code: str | None
    retryable: bool = False
    retry_after_seconds: float | None = None
