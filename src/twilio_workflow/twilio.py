"""Minimal Twilio REST client over stdlib urllib, with retry/backoff.

The transport is injectable so tests and the demo run without network.
Retries cover 429 (Retry-After honored), 5xx, and network errors with
full jitter; 4xx errors other than 429 are permanent and surface
immediately with the Twilio error code attached.
"""
from __future__ import annotations

import base64
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from .errors import TwilioApiError

__all__ = ["TwilioApiError", "TwilioClient", "default_transport"]

PERMANENT_STATUS = {400, 401, 403, 404, 409}
RetrySleep = Callable[[float], None]


def default_transport(method: str, url: str, headers: dict[str, str],
                      body: bytes | None, timeout: float) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()
    except OSError as exc:
        raise TwilioApiError(f"network error: {exc}", retryable=True) from exc


class TwilioClient:
    def __init__(self, account_sid: str, auth_token: str, base_url: str = "https://api.twilio.com",
                 transport: Callable[..., tuple[int, dict[str, str], bytes]] | None = None,
                 timeout: float = 30.0, max_attempts: int = 4,
                 sleep: RetrySleep = time.sleep, rng: random.Random | None = None) -> None:
        self.account_sid = account_sid
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._rng = rng or random.Random()
        token = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        self._transport = transport or default_transport

    def _messages_url(self) -> str:
        return f"{self.base_url}/2010-04-01/Accounts/{self.account_sid}/Messages.json"

    def _request(self, form: dict[str, str]) -> dict[str, Any]:
        body = urllib.parse.urlencode(form).encode()
        delay = 0.5
        last_error: TwilioApiError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                status, headers, payload = self._transport(
                    "POST", self._messages_url(), self._headers, body, self.timeout)
            except OSError as exc:
                error = TwilioApiError(f"network error: {exc}", retryable=True)
                if attempt >= self.max_attempts:
                    raise error from exc
                self._sleep(delay)
                delay *= 2
                continue
            if 200 <= status < 300:
                try:
                    data: dict[str, Any] = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise TwilioApiError(f"invalid JSON from Twilio: {exc}") from exc
                if not isinstance(data, dict):
                    raise TwilioApiError("twilio API returned a non-object JSON response")
                return data
            if status in PERMANENT_STATUS:
                raise TwilioApiError(self._message(status, payload), status=status,
                                     twilio_code=self._code(payload), retryable=False)
            retry_after = None
            if status == 429:
                raw = headers.get("Retry-After") or headers.get("retry-after")
                if raw and raw.replace(".", "", 1).isdigit():
                    retry_after = float(raw)
            error = TwilioApiError(self._message(status, payload), status=status,
                                   twilio_code=self._code(payload), retry_after=retry_after,
                                   retryable=True)
            if attempt >= self.max_attempts:
                raise error
            wait = retry_after if retry_after is not None else delay * (1 + self._rng.random())
            self._sleep(wait)
            delay *= 2
            last_error = error
        raise last_error or TwilioApiError("unreachable", retryable=True)  # pragma: no cover

    @staticmethod
    def _message(status: int, payload: bytes) -> str:
        try:
            data = json.loads(payload)
            detail = data.get("message") or data.get("detail") or ""
            code = data.get("code")
            suffix = f" (code {code})" if code is not None else ""
            return f"twilio API error {status}{suffix}: {detail}"
        except json.JSONDecodeError:
            return f"twilio API error {status}: non-JSON response"

    @staticmethod
    def _code(payload: bytes) -> int | None:
        try:
            code = json.loads(payload).get("code")
            return int(code) if code is not None else None
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def send_sms(self, to: str, from_: str, body: str) -> dict[str, Any]:
        """Send one SMS; returns {sid, status} plus raw fields on error-free responses."""
        data = self._request({"To": to, "From": from_, "Body": body})
        return {"sid": data.get("sid"), "status": data.get("status", "unknown")}
