"""Test helpers: a scripted stand-in for the Anthropic client (no network, no key)."""

from __future__ import annotations

import itertools
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
