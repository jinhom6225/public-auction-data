"""Service key handling (plan D28).

The key comes from the environment only. On GitHub Actions both the raw
and URL-encoded forms are masked before anything else is printed, and
`redact` is a last safety net for log lines.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Mapping, TextIO

from collector.config import KEY_ENV, KEY_PARAM
from collector.errors import ConfigError

_KEY_PARAM_VALUE = re.compile(re.escape(KEY_PARAM) + r"=[^&\s]*")


def read_key(env: Mapping[str, str]) -> str:
    key = env.get(KEY_ENV, "").strip()
    if not key:
        raise ConfigError(f"{KEY_ENV} is not set")
    return key


def key_forms(key: str) -> set[str]:
    """The raw key and every URL-encoded form a request could contain."""
    return {key, urllib.parse.quote(key, safe=""), urllib.parse.quote_plus(key)}


def emit_masks(key: str, env: Mapping[str, str], out: TextIO) -> None:
    """Print `::add-mask::` lines, only when running on GitHub Actions."""
    if env.get("GITHUB_ACTIONS") != "true":
        return
    for form in sorted(key_forms(key)):
        out.write(f"::add-mask::{form}\n")
    out.flush()


def redact(text: str, key: str | None) -> str:
    cleaned = _KEY_PARAM_VALUE.sub(KEY_PARAM + "-redacted", text)
    if key:
        for form in sorted(key_forms(key), key=len, reverse=True):
            cleaned = cleaned.replace(form, "***")
    return cleaned
