from dataclasses import dataclass


@dataclass(frozen=True)
class PageTraversalDecision:
    """Proposed traversal only. The durable collector owns checkpoint advancement."""

    next_offset: int | None
    is_last: bool
    verified_empty: bool
