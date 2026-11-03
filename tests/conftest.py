"""Test helpers: a scripted stand-in for the Anthropic client (no network, no key)."""

from __future__ import annotations

import itertools
import socket
from types import SimpleNamespace

import pytest

_ids = itertools.count(1)


def tool_use(name: str, **inp) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=f"toolu_{next(_ids):03d}", name=name, input=inp)


def text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def response(*blocks, stop: str = "tool_use", inp: int = 1000, out: int = 200, cw: int = 0, cr: int = 0,
             category: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop,
        stop_details=SimpleNamespace(category=category) if stop == "refusal" else None,
        model="fake",
        usage=SimpleNamespace(input_tokens=inp, output_tokens=out, cache_creation_input_tokens=cw,
                              cache_read_input_tokens=cr),
    )


class _Endpoint:
    def __init__(self, client: "FakeClient", kind: str) -> None:
        self.client, self.kind = client, kind

    def create(self, **kwargs):
        # Snapshot the message list: the agent appends to it after the call.
        self.client.calls.append({"endpoint": self.kind, **kwargs, "messages": list(kwargs["messages"])})
        step = self.client.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(kwargs) if callable(step) else step


class FakeClient:
    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[dict] = []
        self.messages = _Endpoint(self, "messages")
        self.beta = SimpleNamespace(messages=_Endpoint(self, "beta"))


@pytest.fixture
def fake():
    return FakeClient


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _no_real_api(monkeypatch, tmp_path):
    """No test can reach the Anthropic API or spend money.

    Removes the API-key variables, points the cost ledger at a temp file and
    refuses every network connection except to this machine (the SDK round-trip
    test talks to a fake API on 127.0.0.1).
    """
    for var in ("ASKDATA_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ASKDATA_COSTS_CSV", str(tmp_path / "costs.csv"))
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def check(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in LOOPBACK:
            raise RuntimeError(f"tests may not open network connections (attempted {address!r})")

    def connect(self, address):
        check(self, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        check(self, address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
