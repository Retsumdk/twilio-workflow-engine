import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from index import Client, Config  # noqa: E402


def test_config_defaults():
    cfg = Config()
    assert cfg["base_url"] == "https://api.example.com"
    assert cfg["retries"] == 3


def test_config_overrides(monkeypatch):
    monkeypatch.setenv("BASE_URL", "https://example.org")
    cfg = Config()
    assert cfg["base_url"] == "https://example.org"


def test_client_run_returns_success():
    client = Client(Config())
    try:
        result = client.run()
    finally:
        client.close()
    assert result["status"] == "success"
