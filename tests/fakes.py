"""A scripted fake of the Anthropic client's messages.create()."""

from __future__ import annotations

import copy
import itertools
from types import SimpleNamespace
from typing import Any, Callable

_ids = itertools.count(1)


def usage(inp=10, out=50, write=0, read=2000, write_1h=0):
    cc = SimpleNamespace(ephemeral_5m_input_tokens=write - write_1h, ephemeral_1h_input_tokens=write_1h)
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_creation_input_tokens=write,
                           cache_read_input_tokens=read, cache_creation=cc)


def thinking():
    return SimpleNamespace(type="thinking", thinking="", signature="sig-abc")


def text(t: str):
    return SimpleNamespace(type="text", text=t)


def tool(name: str, **args: Any):
    return SimpleNamespace(type="tool_use", id=f"toolu_{next(_ids):04d}", name=name, input=args)


def resp(*blocks: Any, stop_reason: str = "tool_use", u: Any = None):
    return SimpleNamespace(content=[thinking(), *blocks], stop_reason=stop_reason, usage=u or usage())


def final(sql: str, answer: str = "Done.", chart_type: str = "none", x: str = "", y: str = ""):
    return resp(tool("final_answer", answer=answer, sql=sql, chart_type=chart_type, chart_x=x, chart_y=y))


class ScriptedClient:
    """Returns the scripted responses in order and records every request."""

    def __init__(self, script: list[Any] | Callable[[dict[str, Any]], Any]) -> None:
        self.script = script
        self.requests: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        snap = dict(kwargs)
        snap["messages"] = copy.copy(kwargs["messages"])  # shallow: keeps block identity
        self.requests.append(snap)
        if callable(self.script):
            return self.script(kwargs)
        if not self.script:
            raise AssertionError("ScriptedClient ran out of responses")
        return self.script.pop(0)
