from dataclasses import dataclass

MODEL_NAME = "google/gemini-3.8-flash"


@dataclass(frozen=True)
class StructuredGenerationRequest:
    task_kind: str
    input_fingerprint: str
    system_prompt: str
    payload_json: str
    schema_name: str
    response_schema_json: str
    model_name: str = MODEL_NAME
