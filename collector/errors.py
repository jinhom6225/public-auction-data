"""Collector errors. Messages never carry URLs, query strings or the key."""

from __future__ import annotations

EXIT_OK = 0
EXIT_CALL_FAILED = 1
EXIT_INVALID = 2
EXIT_BUG = 3
EXIT_USAGE = 64


class CollectError(Exception):
    """Base class; `exit_code` is what collect.py returns."""

    exit_code = EXIT_CALL_FAILED


class ConfigError(CollectError):
    """Missing environment or input that a run cannot do without."""


class ApiError(CollectError):
    """A failed API call: operation name, HTTP status and resultCode only."""

    def __init__(self, operation: str, status: int | None = None,
                 result_code: str | None = None, reason: str | None = None) -> None:
        self.operation = operation
        self.status = status
        self.result_code = result_code
        self.reason = reason
        super().__init__(self._text())

    def _text(self) -> str:
        parts = [f"HTTP {self.status if self.status is not None else '-'}"]
        if self.result_code is not None:
            parts.append(f"resultCode {self.result_code}")
        if self.reason:
            parts.append(self.reason)
        return f"{self.operation} failed: " + ", ".join(parts)


class BudgetExceeded(CollectError):
    """The daily call cap for an API would be passed (D17)."""

    def __init__(self, api: str) -> None:
        self.api = api
        super().__init__(f"{api}: daily call cap reached, not calling")


class ValidationFailed(CollectError):
    """Output that must not be published (D29 2-4, D13)."""

    exit_code = EXIT_INVALID
