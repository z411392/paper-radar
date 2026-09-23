from libs.kernel.ports.publish_object_port import PublishObjectPort
from libs.paper_explanations.exceptions.explanation_verification_error import ExplanationVerificationError


class KernelVerificationReportAdapter:
    def __init__(self, publish: PublishObjectPort) -> None:
        self._publish = publish

    def publish(self, content: bytes) -> str:
        if not isinstance(content, bytes) or not content:
            raise ExplanationVerificationError("invalid_verification_report")
        ref = self._publish(
            content,
            "evidence",
            "application/json; charset=utf-8",
            "verification-report-v1",
        )
        return ref.object_id
