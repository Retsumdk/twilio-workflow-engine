"""Config layering and validation."""
import json

import pytest

from twilio_workflow import ConfigError, load_config


def test_requires_credentials():
    with pytest.raises(ConfigError, match="TWILIO_ACCOUNT_SID"):
        load_config(environ={})


def test_missing_token():
    with pytest.raises(ConfigError, match="TWILIO_AUTH_TOKEN"):
        load_config(environ={"TWILIO_ACCOUNT_SID": "AC123"})


def test_defaults():
    cfg = load_config(environ={"TWILIO_ACCOUNT_SID": "AC123", "TWILIO_AUTH_TOKEN": "tok"})
    assert cfg.account_sid == "AC123"
    assert cfg.auth_token == "tok"
    assert cfg.timeout == 30.0
    assert cfg.max_attempts == 4


def test_env_overrides():
    cfg = load_config(environ={"TWILIO_ACCOUNT_SID": "AC", "TWILIO_AUTH_TOKEN": "t",
                               "TWE_TIMEOUT": "5", "TWE_MAX_ATTEMPTS": "2"})
    assert cfg.timeout == 5.0
    assert cfg.max_attempts == 2


def test_file_then_env_wins(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"timeout": 10, "max_attempts": 7}))
    cfg = load_config(str(path), environ={"TWILIO_ACCOUNT_SID": "AC", "TWILIO_AUTH_TOKEN": "t"})
    assert cfg.timeout == 10.0
    assert cfg.max_attempts == 7
    cfg2 = load_config(str(path), environ={"TWILIO_ACCOUNT_SID": "AC", "TWILIO_AUTH_TOKEN": "t",
                                           "TWE_TIMEOUT": "3"})
    assert cfg2.timeout == 3.0


def test_invalid_file_values(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"timeout": -1}))
    with pytest.raises(ConfigError, match="timeout"):
        load_config(str(bad), environ={"TWILIO_ACCOUNT_SID": "AC", "TWILIO_AUTH_TOKEN": "t"})
    bad2 = tmp_path / "bad2.json"
    bad2.write_text(json.dumps({"max_attempts": 0}))
    with pytest.raises(ConfigError, match="max_attempts"):
        load_config(str(bad2), environ={"TWILIO_ACCOUNT_SID": "AC", "TWILIO_AUTH_TOKEN": "t"})


def test_corrupt_and_missing_file(tmp_path):
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(str(corrupt), environ={"TWILIO_ACCOUNT_SID": "AC", "TWILIO_AUTH_TOKEN": "t"})
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(str(tmp_path / "nope.json"), environ={"TWILIO_ACCOUNT_SID": "AC",
                                                          "TWILIO_AUTH_TOKEN": "t"})
