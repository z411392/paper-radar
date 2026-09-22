from functools import partial

from fire import Fire
from injector import Injector

from apps.cli.adapters.driving.show_version import show_version
from apps.cli.module import CliModule


def run() -> None:
    injector = Injector([CliModule()], auto_bind=False)
    Fire({"version": partial(show_version, injector)})
