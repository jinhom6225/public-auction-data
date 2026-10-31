"""HTTP access for the collector (plan D27, D28).

The transport is injected so tests never touch the network. The client
adds the key, spaces calls (tps), caps the body, refuses redirects and
XML DOCTYPE/ENTITY, and turns every failure into an ApiError that names
the operation, HTTP status and resultCode only.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping

from collector.config import HTTP_TIMEOUT_SECONDS, KEY_PARAM
from collector.errors import ApiError


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


Transport = Callable[[str, float, int], Response]

_DTD = re.compile(rb"<!(DOCTYPE|ENTITY)", re.IGNORECASE)
_XML_CODE = re.compile(rb"<(?:resultCode|returnReasonCode)>\s*([0-9A-Za-z_]{1,10})\s*<")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the key in the query never reaches another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(NoRedirect)


def urllib_transport(url: str, timeout: float, max_bytes: int) -> Response:
    """Real transport. Reads at most max_bytes + 1 so callers see overflow."""
    request = urllib.request.Request(url, headers={"Accept": "application/json, */*"})
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return Response(status=response.status, body=response.read(max_bytes + 1))
    except urllib.error.HTTPError as error:
        body = error.read(max_bytes + 1) if error.fp is not None else b""
        return Response(status=error.code, body=body)


def result_code_of(body: bytes) -> str | None:
    """resultCode / returnReasonCode from a JSON or XML body, if any."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        match = _XML_CODE.search(body[:20000])
        return match.group(1).decode("ascii") if match else None
    return _json_code(data)


def _json_code(data: object) -> str | None:
    if not isinstance(data, dict):
        return None
    for path in (("header", "resultCode"), ("result", "resultCode"),
                 ("OpenAPI_ServiceResponse", "cmmMsgHeader", "returnReasonCode")):
        node: object = data
        for part in path:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, (str, int)) and len(str(node)) <= 10:
            return str(node)
    return None


class ApiClient:
    """Calls one public-data operation with the key from the environment."""

    def __init__(self, transport: Transport, key: str, *, tps: float = 8,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 timeout: float = HTTP_TIMEOUT_SECONDS) -> None:
        self._transport = transport
        self._key = key
        self._interval = 1.0 / tps
        self._monotonic = monotonic
        self._sleep = sleep
        self._timeout = timeout
        self._last_call: float | None = None

    def get(self, operation: str, base_url: str, params: Mapping[str, str], max_bytes: int) -> bytes:
        if not base_url.startswith("https://") or KEY_PARAM in params:
            raise ValueError("refusing a non-https URL or a caller-supplied key")
        url = base_url + "?" + urllib.parse.urlencode({KEY_PARAM: self._key, **params})
        self._wait_turn()
        try:
            response = self._transport(url, self._timeout, max_bytes)
        except Exception:  # noqa: BLE001 - the message could carry the URL
            raise ApiError(operation, reason="transport error") from None
        return self._checked(operation, response, max_bytes)

    def _wait_turn(self) -> None:
        now = self._monotonic()
        if self._last_call is not None:
            pause = self._interval - (now - self._last_call)
            if pause > 0:
                self._sleep(pause)
                now = self._monotonic()
        self._last_call = now

    @staticmethod
    def _checked(operation: str, response: Response, max_bytes: int) -> bytes:
        body = response.body
        if 300 <= response.status < 400:
            raise ApiError(operation, status=response.status, reason="redirect refused")
        if response.status != 200:
            raise ApiError(operation, status=response.status, result_code=result_code_of(body[:max_bytes]))
        if len(body) > max_bytes:
            raise ApiError(operation, status=response.status, reason="body too large")
        if _DTD.search(body):
            raise ApiError(operation, status=response.status, reason="DOCTYPE or ENTITY")
        return body
