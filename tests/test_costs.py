import csv

import pytest

from askdata import config
from askdata.costs import BudgetExceeded, CallUsage, CostLedger, call_cost, worst_case_cost


def u(**kw):
    return CallUsage(**kw)


def test_prices_match_the_pricing_page():
    # $/MTok checked 2026-11-02 on the Anthropic pricing page.
    # Haiku rates apply to prompts up to 100K tokens, so test it per 50K-token prompt.
    assert call_cost(config.HAIKU, u(input_tokens=50_000)) == pytest.approx(0.10 / 20)
    assert call_cost(config.HAIKU, u(output_tokens=1_000_000)) == pytest.approx(0.50)
    assert call_cost(config.SONNET, u(input_tokens=1_000_000, output_tokens=1_000_000)) == pytest.approx(12.0)
    assert call_cost(config.OPUS, u(input_tokens=1_000_000, output_tokens=1_000_000)) == pytest.approx(24.0)
    assert call_cost(config.SONNET, u(cache_read_input_tokens=1_000_000)) == pytest.approx(0.10)
    assert call_cost(config.OPUS, u(cache_read_input_tokens=1_000_000)) == pytest.approx(0.20)
    assert call_cost(config.HAIKU, u(cache_read_input_tokens=50_000)) == pytest.approx(0.01 / 20)
    assert call_cost(config.HAIKU, u(cache_creation_input_tokens=50_000)) == pytest.approx(0.125 / 20)
    assert call_cost(config.OPUS, u(cache_creation_input_tokens=1_000_000)) == pytest.approx(5.0)
    assert call_cost(config.OPUS, u(cache_creation_input_tokens=1_000_000, cache_write_1h_tokens=1_000_000)) == pytest.approx(8.0)


def test_haiku_long_prompt_tier():
    # Prompts over 100K tokens (input + cache reads + cache writes) pay $0.50 / $2.50.
    assert call_cost(config.HAIKU, u(input_tokens=150_000, output_tokens=1_000)) == pytest.approx(0.075 + 0.0025)


def test_ledger_records_running_total(tmp_path):
    path = tmp_path / "c.csv"
    led = CostLedger(path)
    led.record(model=config.SONNET, usage=u(input_tokens=1000, output_tokens=1000), latency_ms=12, stop_reason="end_turn")
    led.record(model=config.SONNET, usage=u(input_tokens=1000, output_tokens=1000), latency_ms=12, stop_reason="end_turn")
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == 2 and float(rows[1]["running_total_usd"]) == pytest.approx(0.024)
    assert CostLedger(path).total_usd == pytest.approx(0.024)  # reloads the total from disk


def test_budget_blocks_before_spending(tmp_path):
    led = CostLedger(tmp_path / "c.csv", budget_usd=0.05)
    led.check(0.04)
    with pytest.raises(BudgetExceeded):
        led.check(0.06)


def test_run_budget(tmp_path):
    led = CostLedger(tmp_path / "c.csv", budget_usd=85, run_budget_usd=0.01)
    led.record(model=config.OPUS, usage=u(output_tokens=400), latency_ms=1, stop_reason="", run_id="r1")
    with pytest.raises(BudgetExceeded, match="run r1"):
        led.check(0.003, run_id="r1")
    led.check(0.003, run_id="r2")


def test_worst_case_covers_max_tokens():
    assert worst_case_cost(config.OPUS, 10_000, 8_000) == pytest.approx((10_000 * 5 + 8_000 * 20) / 1e6)
