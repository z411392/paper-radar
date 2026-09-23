from dataclasses import dataclass, field


@dataclass(frozen=True)
class GenerationReceipt:
    input_fingerprint: str
    request_sha256: str
    generation_id: str | None
    requested_model: str
    returned_model: str | None
    provider: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: str | None
    finish_reason: str | None


@dataclass(frozen=True)
class StructuredGenerationResult:
    content_json: str = field(repr=False)
    receipt: GenerationReceipt
