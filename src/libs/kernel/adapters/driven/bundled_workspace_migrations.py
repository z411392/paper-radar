from importlib.resources import files

from libs.kernel.dtos.migration import Migration
from libs.kernel.exceptions.storage_error import StorageError


def load_workspace_migrations() -> tuple[Migration, ...]:
    name = "0001-object-registry.sql"
    try:
        resource = files("libs.kernel").joinpath("resources", "migrations", name)
        sql = resource.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise StorageError("migration_resource_error", name) from exc
    return (Migration(1, name, sql),)
