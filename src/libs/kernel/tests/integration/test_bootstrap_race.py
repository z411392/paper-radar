from pathlib import Path

from libs.kernel.tests.integration.test_sqlite_workspace import components


def test_bootstrap_race_adopts_database_created_during_directory_scan(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'
    for part in ('state', 'objects', 'tmp'):
        (root / part).mkdir(parents=True)
    original = Path.iterdir
    winner = []
    triggered = False

    def racing_iterdir(path):
        nonlocal triggered
        if path == root / 'state' and not triggered:
            triggered = True
            winner.append(components(root)[0])
        return original(path)

    monkeypatch.setattr(Path, 'iterdir', racing_iterdir)
    assert components(root)[0] == winner[0]
