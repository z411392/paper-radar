from dataclasses import dataclass


@dataclass(frozen=True)
class OpenRouterPolicy:
    max_output_tokens: int
    max_prompt_price: str
    max_completion_price: str
    enabled: bool = False
    max_request_bytes: int = 262144
    max_response_bytes: int = 262144
    timeout_seconds: int = 60
