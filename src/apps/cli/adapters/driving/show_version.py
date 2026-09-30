import json
from dataclasses import asdict

from injector import Injector

from libs.research_workflow.ports.read_runtime_version_port import ReadRuntimeVersionPort


def show_version(injector: Injector) -> None:
    result = injector.get(ReadRuntimeVersionPort)()
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
