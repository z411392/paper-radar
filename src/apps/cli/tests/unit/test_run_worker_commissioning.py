from apps.cli.adapters.driving.run_worker import _parser


def parse(*args: str):
    return _parser().parse_args(["--workspace", "/tmp/workspace", *args])


def test_live_master_switch_does_not_implicitly_enable_a_provider() -> None:
    value = parse("--allow-live-source")
    assert value.rate_limit_state is None
    assert value.ncbi_email is None
    assert value.ncbi_rate_limit_state is None


def test_ncbi_commissioning_arguments_are_independent_from_arxiv() -> None:
    value = parse(
        "--allow-live-source",
        "--ncbi-email",
        "reader@example.com",
        "--ncbi-rate-limit-state",
        "/tmp/ncbi-rate.json",
    )
    assert value.rate_limit_state is None
    assert value.ncbi_email == "reader@example.com"
    assert value.ncbi_rate_limit_state == "/tmp/ncbi-rate.json"


def test_parser_shape_keeps_api_key_optional() -> None:
    value = parse(
        "--allow-live-source",
        "--ncbi-email",
        "reader@example.com",
        "--ncbi-rate-limit-state",
        "/tmp/ncbi-rate.json",
    )
    assert value.ncbi_api_key is None
