import sqlite3
from collections.abc import Callable

from libs.scholarly_catalog.dtos.normalized_identifier import NormalizedIdentifier
from libs.scholarly_catalog.dtos.paper_identity_observation import PaperIdentityObservation
from libs.scholarly_catalog.dtos.paper_identity_resolution import PaperIdentityResolution
from libs.scholarly_catalog.dtos.paper_identity_view import PaperIdentityView
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


class SqlitePaperIdentityStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    def register(
        self,
        observation: PaperIdentityObservation,
        identifier: NormalizedIdentifier,
    ) -> PaperIdentityResolution:
        raise PaperIdentityError("not_implemented")

    def read(self, identifier: NormalizedIdentifier) -> PaperIdentityView:
        raise PaperIdentityError("not_implemented")
