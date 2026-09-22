from pathlib import Path

from injector import Injector

from apps.cli.module import WatchProfileCliModule
from libs.watch_profiles.ports.import_domain_seeds_port import ImportDomainSeedsPort
from libs.watch_profiles.ports.publish_watch_profile_port import PublishWatchProfilePort
from libs.watch_profiles.ports.read_watch_profile_port import ReadWatchProfilePort
from libs.watch_profiles.ports.set_watch_profile_lifecycle_port import SetWatchProfileLifecyclePort


def test_profile_composition_does_not_open_or_create_workspace(tmp_path: Path) -> None:
    root = tmp_path / "not-created"
    injector = Injector([WatchProfileCliModule(str(root))], auto_bind=False)
    assert callable(injector.get(ImportDomainSeedsPort))
    assert callable(injector.get(PublishWatchProfilePort))
    assert callable(injector.get(ReadWatchProfilePort))
    assert callable(injector.get(SetWatchProfileLifecyclePort))
    assert not root.exists()
