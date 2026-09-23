from dataclasses import dataclass, field


@dataclass(frozen=True)
class VerificationFinding:
    code: str
    source_kind: str
    source_index: int
    claim_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeterministicVerificationReport:
    snapshot_id: str
    revision_id: str
    work_id: str
    input_fingerprint: str
    verdict: str
    findings: tuple[VerificationFinding, ...]


@dataclass(frozen=True)
class SupportStatement:
    statement_index: int
    claim_type: str
    text: str = field(repr=False)
    claim_ids: tuple[str, ...]
    source_quotes: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class SupportVerificationRequest:
    snapshot_id: str
    input_fingerprint: str
    statements: tuple[SupportStatement, ...]


@dataclass(frozen=True)
class SupportStatementVerdict:
    statement_index: int
    verdict: str
    claim_ids: tuple[str, ...]


@dataclass(frozen=True)
class SupportVerificationCandidate:
    snapshot_id: str
    input_fingerprint: str
    statements: tuple[SupportStatementVerdict, ...]


@dataclass(frozen=True)
class SupportVerificationResult:
    snapshot_id: str
    input_fingerprint: str
    statements: tuple[SupportStatementVerdict, ...]


@dataclass(frozen=True)
class ExplanationVerificationResult:
    deterministic: DeterministicVerificationReport
    support: SupportVerificationResult | None
    qa_state: str
    support_execution_state: str
