"""Shared test fixtures: scripted transport and frozen clock."""
from __future__ import annotations

import json

import pytest

from twilio_workflow.twilio import TwilioClient


class ScriptedTransport:
    """Queue of scripted HTTP responses; records every call."""

    def __init__(self):
        self.script = []
        self.calls = []

    def enqueue(self, status, payload, headers=None):
        self.script.append((status, payload if isinstance(payload, bytes) else
                            json.dumps(payload).encode(), headers or {}))

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "body": body.decode()})
        if not self.script:
            raise AssertionError("transport called with empty script")
        status, payload, headers = self.script.pop(0)
        return status, headers, payload


class FrozenClock:
    def __init__(self, start=1_760_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


@pytest.fixture
def transport():
    return ScriptedTransport()


@pytest.fixture
def client(transport):
    return TwilioClient("ACtest", "test-token", transport=transport, sleep=lambda s: None,
                        rng=__import__("random").Random(42))


@pytest.fixture
def clock():
    return FrozenClock()
