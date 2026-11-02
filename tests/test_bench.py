import json
import re
from pathlib import Path

import pytest

from bench import run_bench
from bench.build_expected import EXPECTED, compute, load_questions
from bench.grade import eq, grade

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = load_questions()
EXP = json.loads(EXPECTED.read_text(encoding="utf-8"))


def test_question_set_shape():
    assert len(QUESTIONS) == 50
    counts = {d: sum(q["difficulty"] == d for q in QUESTIONS) for d in ("easy", "medium", "hard", "ambiguous")}
    assert counts == {"easy": 18, "medium": 16, "hard": 8, "ambiguous": 8}
    assert len({q["id"] for q in QUESTIONS}) == 50
    assert all(q.get("clarification_reply") for q in QUESTIONS if q["difficulty"] == "ambiguous")


def test_expected_answers_are_current():
    assert compute() == EXP


@pytest.mark.parametrize("q", QUESTIONS, ids=lambda q: q["id"])
def test_gold_passes_its_own_grader(q):
    assert grade(q, EXP[q["id"]]["rows"], EXP[q["id"]]["rows"]) == (True, "ok")


def by_id(i):
    return next(q for q in QUESTIONS if q["id"] == i)


def test_grader_tolerates_presentation_differences():
    e16 = by_id("E16")
    pred = [[float(y), r] for y, r in reversed(EXP["E16"]["rows"])]  # years as numbers, reordered
    assert grade(e16, EXP["E16"]["rows"], [[r, y] for y, r in pred])[0]  # columns swapped
    e10 = by_id("E10")
    split = [n.split(" ", 1) for (n,) in EXP["E10"]["rows"]]  # first and last name in two columns
    assert grade(e10, EXP["E10"]["rows"], split)[0]
    m12 = by_id("M12")
    assert grade(m12, EXP["M12"]["rows"], [[0.2246]])[0]  # share as a fraction
    assert grade(m12, EXP["M12"]["rows"], [[22.46]])[0]  # unrounded percent
    e03 = by_id("E03")
    assert grade(e03, EXP["E03"]["rows"], [["$2,328.60", "extra"]])[0]


def test_grader_rejects_wrong_answers():
    m01 = by_id("M01")
    rows = EXP["M01"]["rows"]
    assert not grade(m01, rows, list(reversed(rows)))[0]  # wrong order for a ranking
    assert not grade(m01, rows, rows[:4])[0]  # missing row
    assert not grade(by_id("E01"), [[59]], [[58]])[0]
    assert not grade(by_id("E01"), [[59]], [])[0]
    assert not grade(by_id("E01"), [[59]], None)[0]
    assert not grade(by_id("H01"), EXP["H01"]["rows"], [[-4.9]])[0]
    assert not eq(2328.6, 2328.7)


def test_dry_run_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run_bench, "ROOT", tmp_path)
    assert run_bench.main(["--dry-run", "--models", "haiku,auto", "--ids", "E01,M03,H02,A01"]) == 0
    out = capsys.readouterr().out
    assert out.count("ok ") == 8 and "BAD" not in out
    results = next((tmp_path / "runs" / "scratch").glob("dry-run-*/results.jsonl")).read_text().splitlines()
    recs = [json.loads(r) for r in results]
    assert len(recs) == 8 and all(r["correct"] for r in recs)
    assert [r["asked_clarification"] for r in recs if r["id"] == "A01"] == [True, True]


def test_paid_run_needs_confirmation_and_deadline(monkeypatch):
    with pytest.raises(SystemExit, match="confirm-spend"):
        run_bench.main(["--models", "haiku", "--ids", "E01"])
    with pytest.raises(SystemExit, match="deadline"):
        run_bench.main(["--models", "haiku", "--ids", "E01", "--confirm-spend", "--deadline", "2020-01-01T00:00:00Z"])


def test_repo_has_no_secrets_contacts_or_default_key_var():
    pattern = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}|[A-Za-z0-9._%+-]+@(gmail|hotmail|yahoo|outlook)\.com", re.I)
    for path in ROOT.rglob("*"):
        if any(p in path.parts for p in (".git", ".venv", "__pycache__", "runs")) or not path.is_file():
            continue
        if path.suffix in {".sqlite", ".pyc"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not pattern.search(text), f"secret or contact detail in {path}"
    src = "\n".join(p.read_text() for p in (ROOT / "src").rglob("*.py"))
    assert "ANTHROPIC_API_KEY" not in src  # the app reads only ASKDATA_ANTHROPIC_KEY
