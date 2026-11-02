from __future__ import annotations

from fastapi.testclient import TestClient

from askdata.agent import AgentResult, ModelUsage
from askdata.web import app as web


class StubAgent:
    def ask(self, question, config="auto"):
        r = AgentResult(question=question, config=config, status="answered", answer="42 orders.",
                        query_id="q1", sql="SELECT 42 AS orders", columns=["orders"], rows=[[42]],
                        row_count=1, model_used="haiku")
        r.usage["haiku"] = ModelUsage(calls=2, input_tokens=100, output_tokens=50, cost_usd=0.0001)
        return r


def test_page_and_meta():
    c = TestClient(web.app)
    assert "Ask your data" in c.get("/").text
    meta = c.get("/api/meta").json()
    assert {m["key"] for m in meta["models"]} == {"auto", "haiku", "sonnet", "opus"}
    assert meta["today"] == "2026-10-01" and len(meta["examples"]) >= 4
    assert c.get("/static/app.js").status_code == 200


def test_ask_returns_the_agent_result(monkeypatch):
    monkeypatch.setattr(web, "get_agent", lambda: StubAgent())
    c = TestClient(web.app)
    data = c.post("/api/ask", json={"question": "How many orders?", "model": "haiku"}).json()
    assert data["status"] == "answered" and data["rows"] == [[42]]
    assert data["model_label"] == "Claude Haiku 5.5" and data["api_calls"] == 2
    assert c.post("/api/ask", json={"question": "How many orders?", "model": "gpt"}).status_code == 400
    assert c.post("/api/ask", json={"question": "", "model": "auto"}).status_code == 422
