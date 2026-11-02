"""The free pre-flight paths (model lookup, count_tokens), with a fake client."""

import csv
import json
from types import SimpleNamespace

from askdata import cli, config
from bench import run_bench


class FakeReal:
    """Stands in for anthropic.Anthropic: only the free endpoints exist."""

    def __init__(self):
        self.count_params = []
        self.messages = SimpleNamespace(count_tokens=self._count, create=self._create)
        self.models = SimpleNamespace(retrieve=lambda m: SimpleNamespace(id=m, display_name=m))

    def _count(self, **params):
        json.dumps(params)  # must be plain JSON, as the SDK would send it
        assert "max_tokens" not in params
        self.count_params.append(params)
        return SimpleNamespace(input_tokens=3000 + len(params["messages"]))

    def _create(self, **kw):
        raise AssertionError("the free checks must never call messages.create")


def test_check_api_is_free_and_logged(monkeypatch, capsys):
    fake = FakeReal()
    monkeypatch.setattr("askdata.agent.make_client", lambda: fake)
    assert cli.main(["check-api"]) == 0
    out = capsys.readouterr().out
    assert "claude-opus-5-5: reachable" in out and "$0.0000" in out
    rows = list(csv.DictReader(config.COSTS_CSV.open()))
    assert len(rows) == 6 and all(float(r["cost_usd"]) == 0 and r["purpose"] == "free-check" for r in rows)


def test_validate_with_api_logs_only_real_calls(monkeypatch, tmp_path):
    fake = FakeReal()
    monkeypatch.setattr("askdata.agent.make_client", lambda: fake)
    monkeypatch.setattr(run_bench, "ROOT", tmp_path)
    assert run_bench.main(["--dry-run", "--validate-with-api", "--models", "haiku", "--ids", "E01,A01"]) == 0
    rows = list(csv.DictReader(config.COSTS_CSV.open()))
    assert rows and all(r["stop_reason"] == "count_tokens" and float(r["cost_usd"]) == 0 for r in rows)
    assert len(rows) == len(fake.count_params) == 5  # E01: 2 calls; A01: clarify, SQL, answer
    counts = json.loads(next((tmp_path / "runs" / "preflight").glob("*/token-counts.json")).read_text())
    assert {c["question_id"] for c in counts} == {"E01", "A01"}
