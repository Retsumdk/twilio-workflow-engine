"""Config layering: defaults < file (JSON) < environment."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from .errors import ConfigError

__all__ = ["Config", "ConfigError", "load_config"]


@dataclass(frozen=True)
class Config:
    account_sid: str
    auth_token: str
    timeout: float = 30.0
    max_attempts: int = 4

    @staticmethod
    def _validate_timeout(value: Any, source: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ConfigError(f"timeout from {source} must be a positive number")
        return float(value)

    @staticmethod
    def _validate_attempts(value: Any, source: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"max_attempts from {source} must be an integer >= 1")
        return value


def load_config(path: str | None = None, environ: dict[str, str] | None = None) -> Config:
    env = os.environ if environ is None else environ
    values: dict[str, Any] = {}
    if path is not None:
        try:
            with open(path, encoding="utf-8") as handle:
                loaded = json.load(handle)
        except OSError as exc:
            raise ConfigError(f"cannot read config file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(f"config file {path} is not valid JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError(f"config file {path} must contain a JSON object")
        for key in ("timeout", "max_attempts"):
            if key in loaded:
                values[key] = loaded[key]
    raw_timeout = env.get("TWE_TIMEOUT", values.get("timeout", 30.0))
    if isinstance(raw_timeout, str):
        try:
            raw_timeout = float(raw_timeout)
        except ValueError:
            raise ConfigError("TWE_TIMEOUT must be a number") from None
    values["timeout"] = Config._validate_timeout(raw_timeout, "TWE_TIMEOUT or config")
    raw_attempts = env.get("TWE_MAX_ATTEMPTS", values.get("max_attempts", 4))
    if isinstance(raw_attempts, str):
        try:
            raw_attempts = int(raw_attempts)
        except ValueError:
            raise ConfigError("TWE_MAX_ATTEMPTS must be an integer") from None
    values["max_attempts"] = Config._validate_attempts(raw_attempts, "TWE_MAX_ATTEMPTS or config")
    sid = env.get("TWILIO_ACCOUNT_SID")
    token = env.get("TWILIO_AUTH_TOKEN")
    if not sid:
        raise ConfigError("missing TWILIO_ACCOUNT_SID (set it in the environment)")
    if not token:
        raise ConfigError("missing TWILIO_AUTH_TOKEN (set it in the environment)")
    return Config(account_sid=sid, auth_token=token, timeout=values["timeout"],
                  max_attempts=values["max_attempts"])
