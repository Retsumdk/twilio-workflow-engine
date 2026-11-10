"""TwilioClient: transport injection, retry classification, jitter backoff."""
import json

import pytest

from twilio_workflow import TwilioApiError, TwilioClient


def make_client(transport, sleeps=None, seed=7, max_attempts=4):
    import random
    return TwilioClient("AC111", "tok", transport=transport, max_attempts=max_attempts,
                        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
                        rng=random.Random(seed))


def test_send_sms_success():
    def transport(method, url, headers, body, timeout):
        assert method == "POST"
        assert "Authorization" in headers and headers["Authorization"].startswith("Basic ")
        assert "To=%2B15551234567" in body.decode() or "To=%2B15551234567" in str(body)
        return 201, {}, json.dumps({"sid": "SMabc", "status": "queued"}).encode()

    client = make_client(transport)
    result = client.send_sms(to="+15551234567", from_="+15550009999", body="hello")
    assert result == {"sid": "SMabc", "status": "queued"}


def test_429_honors_retry_then_succeeds():
    calls = []
    sleeps = []

    def transport(method, url, headers, body, timeout):
        calls.append(1)
        if len(calls) == 1:
            return 429, {"Retry-After": "2"}, json.dumps({"message": "rate limited"}).encode()
        return 201, {}, json.dumps({"sid": "SM1", "status": "queued"}).encode()

    client = make_client(transport, sleeps=sleeps)
    result = client.send_sms("+15551234567", "+15550009999", "hi")
    assert result["sid"] == "SM1"
    assert sleeps == [2.0]


def test_5xx_retries_with_jitter_then_exhausts():
    sleeps = []
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append(1)
        return 500, {}, b"boom"

    client = make_client(transport, sleeps=sleeps, max_attempts=3)
    with pytest.raises(TwilioApiError, match="twilio API error 500") as exc:
        client.send_sms("+15551234567", "+15550009999", "hi")
    assert exc.value.retryable
    assert exc.value.status == 500
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert all(0 < s <= 2.0 for s in sleeps[0:1])  # first backoff <= 2 * base jitter


def test_permanent_4xx_no_retry():
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append(1)
        return 400, {}, json.dumps({"code": 21211, "message": "invalid 'To' number"}).encode()

    client = make_client(transport)
    with pytest.raises(TwilioApiError, match="21211") as exc:
        client.send_sms("+15551234567", "+15550009999", "hi")
    assert not exc.value.retryable
    assert exc.value.twilio_code == 21211
    assert len(calls) == 1


def test_network_error_is_retryable():
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append(1)
        raise OSError("connection reset")

    client = make_client(transport, max_attempts=2)
    with pytest.raises(TwilioApiError, match="network error") as exc:
        client.send_sms("+15551234567", "+15550009999", "hi")
    assert exc.value.retryable
    assert len(calls) == 2


def test_non_json_500_body():
    def transport(method, url, headers, body, timeout):
        return 503, {}, b"<html>down</html>"

    client = make_client(transport, max_attempts=1)
    with pytest.raises(TwilioApiError, match="non-JSON response"):
        client.send_sms("+15551234567", "+15550009999", "hi")


def test_invalid_success_json():
    def transport(method, url, headers, body, timeout):
        return 200, {}, b"{not json"

    client = make_client(transport, max_attempts=1)
    with pytest.raises(TwilioApiError, match="invalid JSON"):
        client.send_sms("+15551234567", "+15550009999", "hi")
