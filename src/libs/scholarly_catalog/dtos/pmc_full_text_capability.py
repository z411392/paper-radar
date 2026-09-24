from dataclasses import dataclass, field


@dataclass(frozen=True)
class PmcFullTextCapability:
    pmcid: str
    available: bool
    automated_retrieval: str
    license_url: str | None
    license_text: str | None
    usage_class: str
    full_text_xml: bytes | None = field(default=None, repr=False)
    response_sha256: str | None = None
    failure_code: str | None = None
