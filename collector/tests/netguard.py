"""Blocks real network access for every test module that imports it.

Each test file imports this module first, so `unittest discover` patches
`socket` before any collector code runs. A test that tries to open a real
connection fails with `NetworkBlocked` instead of calling an API.
"""

import os
import socket


class NetworkBlocked(RuntimeError):
    """Raised when code under test tries to open a real socket."""


class _BlockedSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        raise NetworkBlocked("network access is blocked in collector tests")


def _blocked_create_connection(*args, **kwargs):
    raise NetworkBlocked("network access is blocked in collector tests")


def _blocked_getaddrinfo(*args, **kwargs):
    raise NetworkBlocked("network access is blocked in collector tests")


socket.socket = _BlockedSocket
socket.create_connection = _blocked_create_connection
socket.getaddrinfo = _blocked_getaddrinfo

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))


def fixture_path(*parts: str) -> str:
    """Absolute path of a file under collector/tests/fixtures."""
    return os.path.join(FIXTURES, *parts)
