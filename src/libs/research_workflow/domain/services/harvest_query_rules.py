import json
import re
from typing import Any

from libs.research_workflow.exceptions.harvest_workflow_error import HarvestWorkflowError
from libs.watch_profiles.dtos.domain_definition import DomainDefinition
from libs.watch_profiles.dtos.profile_revision import ProfileRevision


class HarvestQueryRules:
    @staticmethod
    def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise HarvestWorkflowError("invalid_profile_snapshot")
            result[key] = value
        return result

    @staticmethod
    def _identifier(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None:
            raise HarvestWorkflowError("invalid_profile_snapshot")
        return value

    @classmethod
    def profile_domain_revision(cls, profile: ProfileRevision, domain_id: str) -> int:
        if not isinstance(profile, ProfileRevision):
            raise HarvestWorkflowError("invalid_profile_snapshot")
        domain_id = cls._identifier(domain_id)
        domains = profile.domains
        if not isinstance(domains, tuple):
            raise HarvestWorkflowError("invalid_profile_snapshot")
        result: dict[str, int] = {}
        for item in domains:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or cls._identifier(item[0]) != item[0]
                or type(item[1]) is not int
                or not 1 <= item[1] < 2**63
                or item[0] in result
            ):
                raise HarvestWorkflowError("invalid_profile_snapshot")
            result[item[0]] = item[1]
        if domain_id not in result:
            raise HarvestWorkflowError("domain_not_selected")
        return result[domain_id]

    @classmethod
    def filters(cls, profile: ProfileRevision) -> dict[str, Any]:
        try:
            data = json.loads(profile.filters_json, object_pairs_hook=cls._unique)
            expected = {
                "sources",
                "include",
                "exclude",
                "languages",
                "free_only",
                "allow_preprints",
            }
            if not isinstance(data, dict) or set(data) != expected:
                raise HarvestWorkflowError("invalid_profile_snapshot")
            for name in ("sources", "include", "exclude", "languages"):
                values = data[name]
                if (
                    not isinstance(values, list)
                    or any(not isinstance(value, str) or not value for value in values)
                    or len(values) != len(set(values))
                    or values != sorted(values)
                ):
                    raise HarvestWorkflowError("invalid_profile_snapshot")
            if type(data["free_only"]) is not bool or type(data["allow_preprints"]) is not bool:
                raise HarvestWorkflowError("invalid_profile_snapshot")
            return data
        except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
            raise HarvestWorkflowError("invalid_profile_snapshot") from None

    @classmethod
    def domain(cls, value: DomainDefinition, domain_id: str, revision: int) -> None:
        if (
            not isinstance(value, DomainDefinition)
            or value.domain_id != domain_id
            or value.revision != revision
            or cls._identifier(value.domain_id) != value.domain_id
            or type(value.revision) is not int
            or not 1 <= value.revision < 2**63
            or not isinstance(value.sources, tuple)
            or len(value.sources) != len(set(value.sources))
        ):
            raise HarvestWorkflowError("invalid_domain_snapshot")
        for source in value.sources:
            if cls._identifier(source) != source:
                raise HarvestWorkflowError("invalid_domain_snapshot")
        seen: set[str] = set()
        for item in value.source_categories:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or item[0] not in value.sources
                or item[0] in seen
                or not isinstance(item[1], tuple)
                or any(not isinstance(category, str) or not category for category in item[1])
            ):
                raise HarvestWorkflowError("invalid_domain_snapshot")
            seen.add(item[0])
