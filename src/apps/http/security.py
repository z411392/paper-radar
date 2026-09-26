import hmac


class LocalHttpBoundaryError(RuntimeError):
    pass


class LocalHttpSecurityPolicy:
    _ALLOWED_BINDS = {"127.0.0.1", "::1"}

    def __init__(self, bind_host: str, port: int, mutation_token: str) -> None:
        if bind_host not in self._ALLOWED_BINDS:
            raise LocalHttpBoundaryError("non_loopback_bind")
        if type(port) is not int or not 1 <= port <= 65535:
            raise LocalHttpBoundaryError("invalid_local_port")
        if not isinstance(mutation_token, str) or len(mutation_token) < 32:
            raise LocalHttpBoundaryError("invalid_mutation_token")

        self._bind_host = bind_host
        self._port = port
        self._mutation_token = mutation_token

    @property
    def host(self) -> str:
        literal = f"[{self._bind_host}]" if self._bind_host == "::1" else self._bind_host
        return f"{literal}:{self._port}"

    @property
    def origin(self) -> str:
        return f"http://{self.host}"

    def validate_read(self, host: str) -> None:
        if not isinstance(host, str) or not hmac.compare_digest(host, self.host):
            raise LocalHttpBoundaryError("invalid_local_host")

    def validate_mutation(
        self,
        *,
        host: str,
        origin: str,
        mutation_token: str,
    ) -> None:
        self.validate_read(host)
        if not isinstance(origin, str) or not hmac.compare_digest(origin, self.origin):
            raise LocalHttpBoundaryError("invalid_mutation_origin")
        if not isinstance(mutation_token, str) or not hmac.compare_digest(
            mutation_token,
            self._mutation_token,
        ):
            raise LocalHttpBoundaryError("invalid_mutation_token")
