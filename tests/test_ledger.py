"""Cost ledger and budget check: no API calls, no key needed."""

from __future__ import annotations

import csv
import socket
import threading
from types import SimpleNamespace

import pytest

from askdata.agent import Agent, MAX_TOKENS, cost_of
from askdata.config import MODELS
from askdata.ledger import BudgetExceeded, CostLedger, worst_case_usd
from tests.conftest import FakeClient, response, tool_use

SQL = "SELECT COUNT(*) AS orders FROM orders WHERE status = 'completed'"
CHART_NONE = {"type": "none", "x": "", "y": "", "title": ""}


def usage(inp=0, out=0, cw=0, cr=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_creation_input_tokens=cw,
                           cache_read_input_tokens=cr)


def rows(path):
    return list(csv.DictReader(path.open()))


def two_step_script():
    submit = tool_use("submit_answer", answer="There were N completed orders.", query_id="q1",
                      assumptions=["completed orders only"], chart=CHART_NONE, confidence="high")
    return [response(tool_use("run_sql", sql=SQL, purpose="count"), cw=6000, cr=0),
            response(submit, inp=120, out=180, cw=300, cr=6000)]


def test_rows_running_total_and_reload(tmp_path):
    path = tmp_path / "c.csv"
    led = CostLedger(path, budget_usd=10)
    led.record("claude-opus-5-5", usage(inp=1000, out=1000), 0.024, 812, "tool_use")
    led.record("claude-opus-5-5", usage(inp=1000, out=1000), 0.024, 640, "end_turn")
    r = rows(path)
    assert [x["stop_reason"] for x in r] == ["tool_use", "end_turn"]
    assert float(r[-1]["running_total_usd"]) == pytest.approx(0.048)
    assert CostLedger(path).total_usd == pytest.approx(0.048)  # the total survives a restart


def test_reservations_block_concurrent_overshoot(tmp_path):
    led = CostLedger(tmp_path / "c.csv", budget_usd=1.0)
    held = led.reserve(0.6)
    with pytest.raises(BudgetExceeded):
        led.reserve(0.6)  # 0.6 already reserved by a call in flight
    led.release(held)
    led.reserve(0.6)


def test_run_budget(tmp_path):
    led = CostLedger(tmp_path / "c.csv", budget_usd=85, run_budget_usd=0.05)
    led.record("claude-haiku-5-5", usage(out=1000), 0.04, 100, "end_turn")
    with pytest.raises(BudgetExceeded, match="this run"):
        led.reserve(0.02)


def test_worst_case_covers_full_output():
    opus = MODELS["opus"]
    assert worst_case_usd(opus, 10_000, MAX_TOKENS) == pytest.approx((10_000 * 5.00 + MAX_TOKENS * 20.00) / 1e6)
    haiku = MODELS["haiku"]  # prompts over 100K tokens pay Haiku's higher rate card
    assert worst_case_usd(haiku, 150_000, 0) == pytest.approx(150_000 * 0.625 / 1e6)


def test_agent_logs_every_call_with_its_cost(tmp_path):
    led = CostLedger(tmp_path / "c.csv")
    client = FakeClient(two_step_script())
    with led.labelled("q01/haiku"):
        r = Agent(client=client, ledger=led).ask("How many completed orders?", config="haiku")
    logged = rows(tmp_path / "c.csv")
    assert r.status == "answered" and len(logged) == r.api_calls == 2
    assert {x["model"] for x in logged} == {"claude-haiku-5-5"}
    assert {x["label"] for x in logged} == {"q01/haiku"}
    assert sum(float(x["cost_usd"]) for x in logged) == pytest.approx(r.cost_usd, abs=1e-6)
    assert logged[1]["cache_read_tokens"] == "6000"


def test_agent_refuses_before_calling_when_budget_is_too_small(tmp_path):
    client = FakeClient(two_step_script())
    agent = Agent(client=client, ledger=CostLedger(tmp_path / "c.csv", budget_usd=0.001))
    with pytest.raises(BudgetExceeded):
        agent.ask("How many completed orders?", config="opus")
    assert client.calls == []  # nothing was sent
    assert agent.ledger.reserved_usd == 0


def test_injected_clients_get_no_default_ledger():
    assert Agent(client=FakeClient([])).ledger is None


def test_labels_are_per_thread(tmp_path):
    led = CostLedger(tmp_path / "c.csv")

    def work(label):
        with led.labelled(label):
            led.record("claude-haiku-5-5", usage(out=10), 0.0, 1, "end_turn")

    threads = [threading.Thread(target=work, args=(f"q{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(x["label"] for x in rows(tmp_path / "c.csv")) == sorted(f"q{i}" for i in range(8))


def test_cost_matches_the_agent_formula(tmp_path):
    spec = MODELS["sonnet"]
    u = usage(inp=200, out=900, cw=500, cr=8000)
    led = CostLedger(tmp_path / "c.csv")
    led.record(spec.model_id, u, cost_of(spec, u), 10, "end_turn")
    assert float(rows(tmp_path / "c.csv")[0]["cost_usd"]) == pytest.approx(cost_of(spec, u), abs=1e-6)


def test_tests_cannot_reach_the_network():
    with pytest.raises(RuntimeError, match="network"):
        socket.create_connection(("203.0.113.10", 443), timeout=2)  # TEST-NET-3: no DNS lookup needed
