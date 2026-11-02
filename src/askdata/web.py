"""Minimal web UI: FastAPI + one HTML page.

    uvicorn askdata.web:app --port 8000
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .agent import Agent, AgentResult, MissingAPIKey
from .chart import render_svg
from .costs import BudgetExceeded

STATIC = Path(__file__).parent / "static"


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    model: str = "auto"
    chart_width: int | None = Field(default=None, ge=280, le=1200)


class ReplyIn(BaseModel):
    conversation_id: str
    reply: str = Field(min_length=1, max_length=500)
    chart_width: int | None = Field(default=None, ge=280, le=1200)


def _public(res: AgentResult, chart_width: int | None = None) -> dict[str, Any]:
    d = res.to_dict()
    d.pop("trace", None)
    if chart_width and res.status == "answer":
        # Redraw at the page's width so chart text stays legible on phones.
        d["chart_svg"] = render_svg(res.columns, res.rows, res.chart, width=chart_width)
    d["cost_usd"] = round(d["cost_usd"], 6)
    d["latency_ms"] = round(d["latency_ms"])
    return d


def create_app(agent_factory: Callable[[], Agent] | None = None) -> FastAPI:
    app = FastAPI(title="ask-your-data-agent", docs_url=None, redoc_url=None)
    factory = agent_factory or (lambda: Agent(purpose="web"))
    state: dict[str, Any] = {"agent": None}
    owners: OrderedDict[str, Agent] = OrderedDict()
    lock = threading.Lock()

    def agent() -> Agent:
        with lock:
            if state["agent"] is None:
                state["agent"] = factory()
            return state["agent"]

    def guarded(fn: Callable[[], AgentResult], chart_width: int | None = None) -> dict[str, Any]:
        try:
            res = fn()
        except MissingAPIKey as e:
            raise HTTPException(503, str(e)) from None
        except BudgetExceeded as e:
            raise HTTPException(402, str(e)) from None
        except KeyError as e:
            raise HTTPException(404, str(e)) from None
        return _public(res, chart_width)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/ask")
    def ask(body: AskIn) -> dict[str, Any]:
        a = agent()
        out = guarded(lambda: a.ask(body.question.strip(), model=body.model), body.chart_width)
        with lock:
            owners[out["conversation_id"]] = a
            while len(owners) > 500:
                owners.popitem(last=False)
        return out

    @app.post("/api/reply")
    def reply(body: ReplyIn) -> dict[str, Any]:
        a = owners.get(body.conversation_id)
        if a is None:
            raise HTTPException(404, "Unknown or expired conversation")
        return guarded(lambda: a.reply(body.conversation_id, body.reply.strip()), body.chart_width)

    return app


app = create_app()
