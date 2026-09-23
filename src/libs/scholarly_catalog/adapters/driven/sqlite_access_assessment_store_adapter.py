import sqlite3
from collections.abc import Callable

from libs.scholarly_catalog.dtos.access_assessment import AccessAssessment
from libs.scholarly_catalog.dtos.manifestation_access_identity import ManifestationAccessIdentity
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


class SqliteAccessAssessmentStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    def read_manifestation(self, manifestation_id: str) -> ManifestationAccessIdentity:
        raise PaperIdentityError("not_implemented")

    def save(self, assessment: AccessAssessment) -> AccessAssessment:
        raise PaperIdentityError("not_implemented")

    def read_current(self, manifestation_id: str, at: object) -> AccessAssessment:
        raise PaperIdentityError("not_implemented")
