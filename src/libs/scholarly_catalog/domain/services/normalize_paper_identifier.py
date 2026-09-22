from libs.scholarly_catalog.dtos.normalized_identifier import NormalizedIdentifier
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


class NormalizePaperIdentifier:
    def __call__(self, namespace: str, value: str) -> NormalizedIdentifier:
        raise PaperIdentityError("not_implemented")
