from importlib.resources import files

from libs.kernel.dtos.migration import Migration
from libs.kernel.exceptions.storage_error import StorageError


def load_workspace_migrations(*, with_profiles: bool = False) -> tuple[Migration, ...]:
    names = (
        ("0001-object-registry.sql", "0002-watch-profiles.sql")
        if with_profiles
        else ("0001-object-registry.sql",)
    )
    migrations = []
    for version, name in enumerate(names, start=1):
        try:
            resource = files("libs.kernel").joinpath("resources", "migrations", name)
            sql = resource.read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise StorageError("migration_resource_error", name) from exc
        migrations.append(Migration(version, name, sql))
    return tuple(migrations)
