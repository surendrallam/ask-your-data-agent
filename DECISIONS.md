# Decisions

Choices that shape this repo, each with the reason recorded for it. Add a line when you make a new one.

| Date | Decision | Why |
|---|---|---|
| 2026-11-02 | **Synthetic data:** "Trailhead Goods", generated deterministically by `askdata/seed.py` | The data can carry the seasonality, launches, regional problems and ambiguous names (two reps named Priya, two "Summit Supply" accounts, "Apex" as a customer and a brand) that the benchmark needs. Every row is fictional. |
| 2026-11-02 | "Today" is fixed at 2026-10-01; fiscal years run April to March | Relative questions ("last quarter", "active customers") get the same answer every time. |
| 2026-11-02 | **Governed metrics:** 30 glossary definitions and 12 verified example queries, retrieved with BM25 and sent with each question | "Revenue" and the other metrics mean the same thing on every question. |
| 2026-11-02 | **Every answer cites a query id** (`submit_answer`), and the UI shows that exact table and SQL | Answers are traceable to the query that produced them. |
| 2026-11-02 | **The guardrail is enforced by SQLite**, not by string matching: read-only file, `query_only`, an authorizer that allows only reads, one statement, 5-second limit, 1,000 rows | SQLite itself refuses writes, `ATTACH`, `PRAGMA` and `load_extension`; `tests/test_guardrail.py` tries 13 attacks. |
| 2026-11-02 | **Auto mode:** Haiku 5.5 first; the same conversation continues on Sonnet 5.5 after a refusal, the step limit, three SQL errors or a low-confidence answer | Cheap by default. The history is append-only, so Sonnet sees Haiku's queries and results and the cache stays valid. |
| 2026-11-02 | Server-side refusal fallback on Sonnet 5.5 and Opus 5.5 | If a model declines on a policy classifier, the API retries the same request on a fallback model within the same call. |
| 2026-11-02 | **Grading by execution:** the cited SQL is re-run and compared with the reference SQL; counts exactly, money within 0.01%, rates within 0.06 points; a clarifying question on an answerable question is a miss | Grades the numbers, not the wording. |
| 2026-11-02 | Never change a reference answer to make a model pass | Fix the agent, or fix an ambiguous question together with its reference. |
| 2026-11-02 | Prompt v2: "finish with a query that is exactly the answer" | In run 1, 7 of the 9 misses had the right figure inside a wider table. |
| 2026-11-02 | The API key is read from `ASKDATA_ANTHROPIC_API_KEY` or the project's `.env`, never exported as `ANTHROPIC_API_KEY` in the shell | Claude Code would use `ANTHROPIC_API_KEY` for its own requests. |
| 2026-11-03 | **CI on every push and pull request** (Python 3.10 to 3.13): seed the database, run the tests, dry-run the reference SQL | Catches breakage before it reaches `main`. It needs no API key and makes no API calls. |
| 2026-11-03 | **Tests can't reach the network:** `tests/conftest.py` removes the key variables and refuses every connection except to 127.0.0.1 | A real key in a developer's `.env` can't turn a test into a paid call. The SDK round-trip test talks to its fake API on 127.0.0.1. |
| 2026-11-03 | **Per-call cost ledger with a hard budget check** (`askdata/ledger.py`): every API call is appended to `runs/costs.csv`; before each call its worst case (whole prompt at the dearer input or cache-write rate, plus full `max_tokens` output) is reserved and the call is refused if that could pass the budget | `bench.run --budget` is a soft stop between questions, and four questions run at once. The reservation makes the budget a hard limit per call, including concurrent ones. |
| 2026-11-03 | Ledger defaults: on when the agent builds its own client (CLI, web UI); `bench.run` passes its own; off for injected clients (tests); total budget $85 (`ASKDATA_BUDGET_USD`) | $85 was the spend cap set for this project. The ledger counts calls from 3 Nov 2026 on; earlier runs' costs are in `bench/results/*/results.jsonl`. |
| 2026-11-03 | **Sonnet 5.5 cache reads priced at $0.10 per million tokens** (0.05x input, as on Opus 5.5); stored results corrected from their token counts by `bench/reprice.py` (PR #3) | The first version used $0.20, which overstated Sonnet's published cost by about 13%. The pricing page is the source; `tests/test_agent_offline.py` checks the multipliers. |
