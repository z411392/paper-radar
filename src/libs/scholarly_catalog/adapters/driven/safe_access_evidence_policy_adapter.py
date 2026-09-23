import hashlib
import ipaddress
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

from libs.scholarly_catalog.domain.services.normalize_paper_identifier import NormalizePaperIdentifier
from libs.scholarly_catalog.dtos.access_assessment import AccessAssessment
from libs.scholarly_catalog.dtos.access_location_claim import AccessLocationClaim
from libs.scholarly_catalog.dtos.access_location_probe import AccessLocationProbe
from libs.scholarly_catalog.dtos.manifestation_access_identity import ManifestationAccessIdentity
from libs.scholarly_catalog.exceptions.access_assessment_error import AccessAssessmentError
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


class SafeAccessEvidencePolicyAdapter:
    _SCOPES = {"abstract", "full_text"}
    _READER_ACCESS = {"free", "restricted", "unknown"}
    _AUTOMATED = {"permitted", "prohibited", "unknown"}
    _USES = {
        "local_reading",
        "local_storage",
        "local_research_processing",
        "external_model_processing",
        "redistribution",
    }

    def __init__(self, allowed_evidence_sources: frozenset[str]) -> None:
        if (
            not isinstance(allowed_evidence_sources, frozenset)
            or not allowed_evidence_sources
            or any(not isinstance(item, str) or not item for item in allowed_evidence_sources)
        ):
            raise AccessAssessmentError("invalid_access_policy")
        self._allowed_evidence_sources = allowed_evidence_sources
        self._normalize = NormalizePaperIdentifier()

    @staticmethod
    def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise AccessAssessmentError("invalid_access_evidence")
            result[key] = value
        return result

    @staticmethod
    def _constant(_value: str) -> None:
        raise AccessAssessmentError("invalid_access_evidence")

    @classmethod
    def _evidence(cls, value: str) -> object:
        if not isinstance(value, str):
            raise AccessAssessmentError("invalid_access_evidence")
        try:
            if not 1 <= len(value.encode("utf-8")) <= 32768:
                raise AccessAssessmentError("invalid_access_evidence")
            decoded = json.loads(
                value,
                object_pairs_hook=cls._unique,
                parse_constant=cls._constant,
            )
        except (json.JSONDecodeError, UnicodeEncodeError, RecursionError) as exc:
            raise AccessAssessmentError("invalid_access_evidence") from exc
        if not isinstance(decoded, dict) or not decoded:
            raise AccessAssessmentError("invalid_access_evidence")
        return decoded

    @staticmethod
    def _json(value: object) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise AccessAssessmentError("invalid_access_evidence") from exc

    @staticmethod
    def _time(value: datetime | None, *, required: bool = False) -> str | None:
        if value is None:
            if required:
                raise AccessAssessmentError("invalid_access_time")
            return None
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise AccessAssessmentError("invalid_access_time")
        try:
            return value.astimezone(timezone.utc).isoformat()
        except (OverflowError, ValueError) as exc:
            raise AccessAssessmentError("invalid_access_time") from exc

    @staticmethod
    def _safe_url(value: str) -> None:
        if not isinstance(value, str) or not value or len(value) > 8192:
            raise AccessAssessmentError("unsafe_access_target")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise AccessAssessmentError("unsafe_access_target") from exc
        host = parsed.hostname
        if (
            parsed.scheme != "https"
            or host is None
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.fragment
        ):
            raise AccessAssessmentError("unsafe_access_target")
        lowered = host.lower().rstrip(".")
        if lowered == "localhost" or lowered.endswith(".localhost") or lowered.endswith(".local"):
            raise AccessAssessmentError("unsafe_access_target")
        try:
            address = ipaddress.ip_address(lowered)
        except ValueError:
            return
        if not address.is_global:
            raise AccessAssessmentError("unsafe_access_target")

    @staticmethod
    def _public_ips(values: tuple[str, ...]) -> None:
        if not isinstance(values, tuple) or not values or len(values) > 16:
            raise AccessAssessmentError("unsafe_access_target")
        for value in values:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise AccessAssessmentError("unsafe_access_target") from exc
            if not address.is_global:
                raise AccessAssessmentError("unsafe_access_target")

    @classmethod
    def _network_hops(
        cls,
        requested_url: str,
        redirect_chain: tuple[str, ...],
        final_url: str,
        network_hops: tuple[tuple[str, tuple[str, ...]], ...],
    ) -> None:
        expected_urls = (requested_url, *redirect_chain)
        if not redirect_chain or redirect_chain[-1] != final_url:
            if final_url != requested_url:
                raise AccessAssessmentError("invalid_access_probe")
        if final_url != expected_urls[-1]:
            expected_urls = (*expected_urls, final_url)
        if (
            not isinstance(network_hops, tuple)
            or len(network_hops) != len(expected_urls)
            or len(network_hops) > 6
        ):
            raise AccessAssessmentError("invalid_access_probe")
        seen: set[str] = set()
        for expected, hop in zip(expected_urls, network_hops, strict=True):
            if (
                not isinstance(hop, tuple)
                or len(hop) != 2
                or hop[0] != expected
                or hop[0] in seen
                or not isinstance(hop[1], tuple)
            ):
                raise AccessAssessmentError("invalid_access_probe")
            cls._safe_url(hop[0])
            cls._public_ips(hop[1])
            seen.add(hop[0])

    def _observed_identifier(
        self,
        identity: ManifestationAccessIdentity,
        probe: AccessLocationProbe,
    ):
        namespace, value = probe.observed_identifier_namespace, probe.observed_identifier_value
        if (namespace is None) != (value is None):
            raise AccessAssessmentError("invalid_access_probe")
        if namespace is None or value is None:
            return None
        try:
            observed = self._normalize(namespace, value)
        except PaperIdentityError as exc:
            raise AccessAssessmentError("access_identity_mismatch") from exc
        if (
            observed.namespace != identity.source_namespace
            or observed.normalized_value != identity.native_id
        ):
            raise AccessAssessmentError("access_identity_mismatch")
        return observed

    def evaluate(
        self,
        identity: ManifestationAccessIdentity,
        claim: AccessLocationClaim,
        probe: AccessLocationProbe,
    ) -> AccessAssessment:
        if not isinstance(identity, ManifestationAccessIdentity):
            raise AccessAssessmentError("invalid_manifestation_identity")
        if not isinstance(claim, AccessLocationClaim) or not isinstance(probe, AccessLocationProbe):
            raise AccessAssessmentError("invalid_access_evidence")
        if claim.manifestation_id != identity.manifestation_id:
            raise AccessAssessmentError("access_identity_mismatch")
        if claim.evidence_source_id not in self._allowed_evidence_sources:
            raise AccessAssessmentError("unsupported_access_evidence_source")
        if (
            claim.content_scope not in self._SCOPES
            or claim.reader_access_signal not in self._READER_ACCESS
            or claim.automated_retrieval_signal not in self._AUTOMATED
            or not isinstance(claim.permitted_uses, tuple)
            or len(claim.permitted_uses) != len(set(claim.permitted_uses))
            or not set(claim.permitted_uses) <= self._USES
        ):
            raise AccessAssessmentError("invalid_access_evidence")

        source_evidence = self._evidence(claim.evidence_json)
        checked = self._time(claim.checked_at, required=True)
        expires = self._time(claim.expires_at)
        assert checked is not None
        if expires is not None and expires <= checked:
            raise AccessAssessmentError("invalid_access_time")

        self._safe_url(claim.location_url)
        if probe.requested_url != claim.location_url:
            raise AccessAssessmentError("invalid_access_probe")
        if (
            not isinstance(probe.redirect_chain, tuple)
            or len(probe.redirect_chain) > 5
            or type(probe.http_status) is not int
            or not 100 <= probe.http_status <= 599
        ):
            raise AccessAssessmentError("invalid_access_probe")
        for url in (probe.requested_url, *probe.redirect_chain, probe.final_url):
            self._safe_url(url)
        expected_final = probe.redirect_chain[-1] if probe.redirect_chain else probe.requested_url
        if probe.final_url != expected_final:
            raise AccessAssessmentError("invalid_access_probe")
        self._network_hops(
            probe.requested_url,
            probe.redirect_chain,
            probe.final_url,
            probe.network_hops,
        )

        observed = self._observed_identifier(identity, probe)
        identity_matches = observed is not None
        if claim.content_version_binding is not None:
            if not isinstance(claim.content_version_binding, str) or not claim.content_version_binding:
                raise AccessAssessmentError("invalid_access_evidence")
            if observed is None:
                raise AccessAssessmentError("access_version_mismatch")
            suffix = f"v{observed.native_version}" if observed.native_version is not None else ""
            expected_binding = f"{observed.namespace}:{observed.normalized_value}{suffix}"
            if claim.content_version_binding != expected_binding:
                raise AccessAssessmentError("access_version_mismatch")

        if probe.content_type is not None and (
            not isinstance(probe.content_type, str) or len(probe.content_type) > 256
        ):
            raise AccessAssessmentError("invalid_access_probe")
        full_text_type = probe.content_type in {
            "application/pdf",
            "text/html",
            "application/xhtml+xml",
        }
        success = probe.http_status in {200, 206} and identity_matches
        if success and claim.content_scope == "full_text" and not full_text_type:
            raise AccessAssessmentError("unsupported_access_content")
        reader_access = claim.reader_access_signal if success else "unknown"
        automated = claim.automated_retrieval_signal
        uses = tuple(sorted(claim.permitted_uses))

        evidence = self._json(
            {
                "format_version": 1,
                "evidence_source_id": claim.evidence_source_id,
                "content_scope": claim.content_scope,
                "source_evidence": source_evidence,
                "signals": {
                    "reader_access": claim.reader_access_signal,
                    "automated_retrieval": claim.automated_retrieval_signal,
                    "permitted_uses": list(sorted(claim.permitted_uses)),
                },
                "probe": {
                    "requested_url": probe.requested_url,
                    "final_url": probe.final_url,
                    "redirect_chain": list(probe.redirect_chain),
                    "network_hops": [
                        [url, list(ips)]
                        for url, ips in probe.network_hops
                    ],
                    "http_status": probe.http_status,
                    "content_type": probe.content_type,
                    "observed_identifier_namespace": probe.observed_identifier_namespace,
                    "observed_identifier_value": probe.observed_identifier_value,
                    "identity_matches": identity_matches,
                },
            }
        )
        assessment_id = "access:" + hashlib.sha256(
            self._json(
                {
                    "manifestation_id": claim.manifestation_id,
                    "location_url": probe.final_url,
                    "checked_at": checked,
                    "content_version_binding": claim.content_version_binding,
                    "evidence": evidence,
                }
            ).encode("utf-8")
        ).hexdigest()
        return AccessAssessment(
            assessment_id,
            claim.manifestation_id,
            probe.final_url,
            claim.content_scope,
            reader_access,
            automated,
            uses,
            claim.license_id,
            evidence,
            checked,
            expires,
            claim.content_version_binding,
        )
