from dataclasses import dataclass, field


@dataclass(frozen=True)
class PreparedHarvestCapture:
    metadata_json: str = field(repr=False)
    raw_object_id: str | None
    body: bytes | None = field(repr=False)
