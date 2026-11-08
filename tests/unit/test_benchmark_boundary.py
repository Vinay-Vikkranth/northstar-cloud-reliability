import argparse
import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location("northstar_benchmark",Path(__file__).resolve().parents[2]/"scripts/benchmark.py")
benchmark=importlib.util.module_from_spec(spec);spec.loader.exec_module(benchmark)

@pytest.mark.parametrize("url",["https://api.hubapi.com","http://example.com","http://localhost.example.com","http://user:password@localhost:1","http://127.0.0.1:1/?token=secret","http://127.0.0.1:1/unsafe"])
def test_benchmark_cannot_target_external_or_credential_bearing_origins(url):
    with pytest.raises(argparse.ArgumentTypeError):benchmark.local_url(url)

@pytest.mark.parametrize("url",["http://127.0.0.1:28601","http://localhost:28601","http://[::1]:28601"])
def test_benchmark_accepts_only_plain_local_origins(url):
    assert benchmark.local_url(url)==url
