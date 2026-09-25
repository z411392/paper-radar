from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class EmbeddingSpaceInput:
    provider: str
    model_name: str
    model_revision: str
    dimension: int
    dtype: str
    normalization_version: str
    prefix_config_hash: str
    metric: str


@dataclass(frozen=True)
class PreparedEmbeddingSpace:
    space_id: str
    provider: str
    model_name: str
    model_revision: str
    dimension: int
    dtype: str
    normalization_version: str
    prefix_config_hash: str
    metric: str
    configuration_fingerprint: str


@dataclass(frozen=True)
class RegisteredEmbeddingSpace:
    space_id: str
    provider: str
    model_name: str
    model_revision: str
    dimension: int
    dtype: str
    normalization_version: str
    prefix_config_hash: str
    metric: str
    configuration_fingerprint: str
    created_at: datetime
    replayed: bool
