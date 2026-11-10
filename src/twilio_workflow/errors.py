"""Error hierarchy: TweError root; specific subclasses map to CLI exit codes."""
from __future__ import annotations


class TweError(Exception):
    """Base class for all twilio-workflow-engine errors."""


class ConfigError(TweError):
    """Invalid or missing configuration."""


class WorkflowError(TweError):
    """Invalid workflow definition or run argument."""


class TemplateError(TweError):
    """Template rendering failed (missing variable)."""


class StateError(TweError):
    """State journal is missing, corrupt, or tampered."""


class TwilioApiError(TweError):
    """Twilio REST API failure; `retryable` says whether a retry could help."""

    def __init__(self, message: str, *, status: int | None = None,
                 twilio_code: int | None = None, retry_after: float | None = None,
                 retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.twilio_code = twilio_code
        self.retry_after = retry_after
        self.retryable = retryable
