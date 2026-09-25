from importlib.resources import files

from libs.kernel.dtos.migration import Migration
from libs.kernel.exceptions.storage_error import StorageError


def load_workspace_migrations(
    *,
    with_profiles: bool = False,
    with_discovery: bool = False,
    with_runtime: bool = False,
) -> tuple[Migration, ...]:
    if any(type(value) is not bool for value in (with_profiles, with_discovery, with_runtime)):
        raise StorageError("invalid_migrations", "schema selectors must be booleans")
    if with_runtime:
        names = (
            "0001-object-registry.sql",
            "0002-watch-profiles.sql",
            "0003-scholarly-catalog.sql",
            "0004-discovery.sql",
            "0005-paper-explanations.sql",
            "0006-retrieval.sql",
            "0007-delivery.sql",
            "0008-workflow-jobs.sql",
            "0009-relevance-assessment-domains.sql",
            "0010-pubmed-harvest.sql",
            "0011-crossref-harvest.sql",
            "0012-crossref-repair.sql",
            "0013-crossref-window-splits.sql",
            "0014-crossref-capture-claims.sql",
            "0015-crossref-capture-inbox.sql",
            "0016-crossref-capture-resolutions.sql",
            "0017-crossref-capture-recoveries.sql",
            "0018-crossref-provider-revisions.sql",
            "0019-crossref-projection-quarantines.sql",
            "0020-crossref-relation-assertions.sql",
            "0021-crossref-integrity-assertions.sql",
            "0022-crossref-integrity-work-bindings.sql",
            "0023-delivery-digest-rebuilds.sql",
            "0024-source-catalog-projection.sql",
        )
    elif with_discovery:
        names = (
            "0001-object-registry.sql",
            "0002-watch-profiles.sql",
            "0003-scholarly-catalog.sql",
            "0004-discovery.sql",
        )
    elif with_profiles:
        names = ("0001-object-registry.sql", "0002-watch-profiles.sql")
    else:
        names = ("0001-object-registry.sql",)
    migrations = []
    for version, name in enumerate(names, start=1):
        try:
            resource = files("libs.kernel").joinpath("resources", "migrations", name)
            sql = resource.read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise StorageError("migration_resource_error", name) from exc
        migrations.append(Migration(version, name, sql))
    return tuple(migrations)
