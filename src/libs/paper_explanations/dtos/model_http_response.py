from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelHttpResponse:
    status: int
    body: bytes = field(repr=False)
