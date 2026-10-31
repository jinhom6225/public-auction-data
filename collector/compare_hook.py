"""Seam where Phase 3 plugs in the real-transaction comparison (research §3.4).

`attach_compare` receives every normalised item (with the Phase 3 metadata:
usage code, RTMS API alias, raw rounds) and returns new records with a
`compare` field. Phase 2 has no comparison yet, so every item gets
`{"kind": "none"}`. Phase 3 replaces the body of `attach_compare` (and adds
its own inputs, e.g. the RTMS cache under `cache/rtms/`) without touching
the rest of collect.py.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Iterable, Mapping

NO_COMPARE: Mapping[str, str] = MappingProxyType({"kind": "none"})


def attach_compare(items: Iterable, context: object = None) -> list[dict]:
    """Records with `compare` set; Phase 2: always kind none."""
    return [{**item.record, "compare": dict(NO_COMPARE)} for item in items]
