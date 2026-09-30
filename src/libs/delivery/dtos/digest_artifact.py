from dataclasses import dataclass


@dataclass(frozen=True)
class StoredDigestPayload:
    subscription_id: str
    period_key: str
    content_fingerprint: str
    subject: str
    text_body: str
    html_body: str
