"""Test isolation: no API key, no Anthropic client, no network, a temp cost ledger.

Every test runs with these guards, so the suite (and CI) cannot spend money.
"""

from __future__ import annotations

import socket

import pytest

from askdata import config


class NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.delenv(config.API_KEY_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "COSTS_CSV", tmp_path / "costs.csv")

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def guard(addr):
        if isinstance(addr, tuple):  # AF_INET / AF_INET6; Unix sockets and socketpairs stay allowed
            raise NetworkBlocked(f"network access attempted in tests: {addr!r}")

    def connect(self, addr):
        guard(addr)
        return real_connect(self, addr)

    def connect_ex(self, addr):
        guard(addr)
        return real_connect_ex(self, addr)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)

    import anthropic

    def no_client(*args, **kwargs):
        raise AssertionError("tests must never construct a real Anthropic client")

    monkeypatch.setattr(anthropic, "Anthropic", no_client)
    yield
