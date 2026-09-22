from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.discovery.ports.source_query_compiler_port import SourceQueryCompilerPort


class CompileSourceQuery:
    def __init__(self, compiler: SourceQueryCompilerPort) -> None:
        self._compiler = compiler

    def __call__(self, query: SourceQueryInput) -> CompiledSourceQuery:
        return self._compiler.compile(query)
