"""Round trip through the real Anthropic SDK against a local fake Messages API.

The scripted-client tests check the loop's logic; this one checks the plumbing:
the SDK accepts every request parameter the agent sends, serializes returned
blocks (thinking, tool_use) back into valid JSON, and parses the responses.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import anthropic
import pytest

from askdata.agent import Agent


def _msg(content, stop="tool_use", model="claude-haiku-5-5"):
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": model, "content": content,
        "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 50, "output_tokens": 40, "cache_creation_input_tokens": 3000,
                  "cache_read_input_tokens": 0},
    }


THINK = {"type": "thinking", "thinking": "", "signature": "c2lnbmF0dXJl"}


class FakeAPI:
    def __init__(self, script):
        self.script = list(script)
        self.requests: list[tuple[str, dict, dict]] = []
        handler = self._handler()
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handler(self):
        api = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                api.requests.append((self.path, dict(self.headers), body))
                payload = json.dumps(api.script.pop(0)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        return H

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake_api():
    apis = []

    def make(script):
        api = FakeAPI(script)
        apis.append(api)
        return api

    yield make
    for a in apis:
        a.close()


def test_auto_mode_round_trip_through_the_sdk(fake_api):
    sql = "SELECT COUNT(*) AS orders FROM orders WHERE status = 'completed'"
    chart = {"type": "none", "x": "", "y": "", "title": ""}
    api = fake_api([
        # Haiku: one query, then a low-confidence answer -> escalate
        _msg([THINK, {"type": "tool_use", "id": "toolu_1", "name": "run_sql", "input": {"sql": sql, "purpose": "count"}}]),
        _msg([THINK, {"type": "text", "text": "Unsure."},
              {"type": "tool_use", "id": "toolu_2", "name": "submit_answer",
               "input": {"answer": "x", "query_id": "q1", "assumptions": [], "chart": chart, "confidence": "low"}}]),
        # Sonnet (beta endpoint, fallbacks on): answers with high confidence
        _msg([THINK, {"type": "tool_use", "id": "toolu_3", "name": "submit_answer",
                      "input": {"answer": "29,712 completed orders.", "query_id": "q1", "assumptions": ["completed only"],
                                "chart": chart, "confidence": "high"}}], model="claude-sonnet-5-5"),
    ])
    client = anthropic.Anthropic(api_key="test-key", base_url=api.url, max_retries=0)
    r = Agent(client=client).ask("How many completed orders?", config="auto")
    assert r.status == "answered" and r.escalated and r.model_used == "sonnet"
    assert r.rows[0][0] > 0

    assert all(p.startswith("/v1/messages") for p, _, _ in api.requests)
    h1 = {k.lower(): v for k, v in api.requests[0][1].items()}
    assert "server-side-fallback" not in h1.get("anthropic-beta", "")  # Haiku: no fallback
    h3 = {k.lower(): v for k, v in api.requests[2][1].items()}
    assert "server-side-fallback-2026-07-01" in h3.get("anthropic-beta", "")
    # Only the project's key is sent (SDK 2.x uses a Bearer header; older SDKs used x-api-key).
    assert h3.get("authorization") == "Bearer test-key" or h3.get("x-api-key") == "test-key"

    first = api.requests[0][2]
    assert first["model"] == "claude-haiku-5-5" and first["max_tokens"] == 16000
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert first["output_config"] == {"effort": "medium"}
    assert all(t["strict"] is True for t in first["tools"])
    assert "thinking" not in first and "temperature" not in first

    sonnet = api.requests[2][2]
    assert sonnet["model"] == "claude-sonnet-5-5" and sonnet["fallbacks"] == "default"
    roles = [m["role"] for m in sonnet["messages"]]
    assert roles == ["user", "assistant", "user", "assistant", "user", "system"]
    # Haiku's thinking blocks go back unchanged, signature included.
    assert sonnet["messages"][1]["content"][0] == THINK
    # Every tool_use gets exactly one tool_result in the next user turn.
    for i, m in enumerate(sonnet["messages"]):
        if m["role"] == "assistant":
            uses = {b["id"] for b in m["content"] if b["type"] == "tool_use"}
            results = {b["tool_use_id"] for b in sonnet["messages"][i + 1]["content"] if b["type"] == "tool_result"}
            assert uses == results
    # The history Sonnet sees starts with exactly what Haiku sent (append-only).
    haiku_last = api.requests[1][2]["messages"]
    assert sonnet["messages"][: len(haiku_last)] == haiku_last
