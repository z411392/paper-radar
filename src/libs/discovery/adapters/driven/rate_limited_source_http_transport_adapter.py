import time
from collections.abc import Callable

from libs.discovery.adapters.driven.parse_retry_after import parse_retry_after
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.source_fetch_error import SourceFetchError
from libs.discovery.ports.source_http_transport_port import SourceHttpTransportPort
from libs.discovery.ports.source_rate_limit_port import SourceRateLimitPort


class RateLimitedSourceHttpTransportAdapter:
    LOCAL_DEFERRAL_MAX_SECONDS = 1.0
    LOCAL_DEFERRAL_RETRIES = 2

    def __init__(
        self,
        transport: SourceHttpTransportPort,
        gate: SourceRateLimitPort,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport
        self._gate = gate
        self._sleep = sleep

    def get(self, request: SourcePageRequest) -> SourceHttpResponse:
        for attempt in range(self.LOCAL_DEFERRAL_RETRIES + 1):
            try:
                with self._gate.slot() as lease:
                    response = self._transport.get(request)
                    if response.status == 429:
                        values = [
                            value
                            for key, value in response.headers
                            if key.lower() == "retry-after"
                        ]
                        if len(values) > 1:
                            lease.defer(60.0)
                            raise SourceFetchError("invalid_retry_after")
                        delay = (
                            parse_retry_after(values[0], response.received_at)
                            if values
                            else 1.0
                        )
                        lease.defer(delay)
                    elif 500 <= response.status < 600:
                        lease.defer(1.0)
                    return response
            except SourceFetchError as exc:
                delay = exc.retry_after_seconds
                if (
                    exc.code == "provider_deferred"
                    and exc.retryable
                    and delay is not None
                    and 0 < delay <= self.LOCAL_DEFERRAL_MAX_SECONDS
                    and attempt < self.LOCAL_DEFERRAL_RETRIES
                ):
                    self._sleep(delay)
                    continue
                raise
        raise SourceFetchError("provider_deferred")
