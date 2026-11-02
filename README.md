# ask-your-data-agent

Ask a sales database a question in plain English. The agent looks up business terms in a glossary, writes one read-only SQL query, runs it under guardrails, and answers with the numbers, a chart and the SQL it used. When a question is ambiguous ("Who is our best customer?") it asks one clarifying question first.

> **Status:** build complete, benchmark not yet run. The results tables (accuracy, cost per question and median latency for Claude Haiku 5.5, Sonnet 5.5 and Opus 5.5 on 50 test questions) will be added here from the benchmark runs.

## What's inside

- **Claude API tool use** with four tools: `search_glossary`, `run_sql`, `ask_clarification`, `final_answer`.
- **Prompt caching:** the system prompt (instructions, schema, tools) is cached, and so is the growing conversation.
- **Model routing:** Haiku 5.5 by default; hard questions go to Sonnet 5.5, and Haiku's attempt restarts on Sonnet 5.5 if its first SQL fails.
- **Guardrails:** read-only connection, a SQLite authorizer that allows only reads, one `SELECT` per query, a 200-row limit and a 5-second timeout. Everything else is rejected.
- **Business glossary (RAG):** 30 definitions such as "active customer", "region" and "last quarter", retrieved with BM25 and mapped to SQL.
- **MCP server** exposing the same tools to Claude Desktop.
- **Web UI:** FastAPI and one HTML page. Also a CLI.
- **Cost ledger:** every API call is logged to `runs/costs.csv` with model, tokens, cost, latency and a running total.
- **Tests:** pytest with a scripted fake client. CI never calls the API.

## Run it

Python 3.11+.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Free commands (no API key needed):

```bash
askdata sql "SELECT BillingCountry, ROUND(SUM(Total), 2) AS revenue FROM Invoice GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
askdata glossary "active customer"
python -m bench.run_bench --dry-run
```

Questions in plain English call the Claude API. Put your key in the `ASKDATA_ANTHROPIC_KEY` environment variable (the app reads no other variable), then:

```bash
askdata ask "Which 5 countries brought in the most revenue in 2025?"
uvicorn askdata.web:app --port 8000   # then open http://localhost:8000
```

### Claude Desktop (MCP)

The MCP server needs no API key: Claude Desktop is the model. Add this to `claude_desktop_config.json`, with the absolute path to your virtual environment:

```json
{
  "mcpServers": {
    "ask-your-data": { "command": "/absolute/path/to/.venv/bin/askdata-mcp" }
  }
}
```

## Data

The [Chinook sample database](https://github.com/lerocha/chinook-database) (a digital music store's customers, invoices and catalogue, 2021–2025), MIT licence, copyright Luis Rocha; the notice is in [`data/LICENSE-chinook.md`](data/LICENSE-chinook.md). `scripts/build_db.py` rebuilds `data/chinook.sqlite` from the upstream file (checked by SHA-256) and removes street addresses, postal codes, phone, fax, email and birth-date columns.

## Benchmark

`bench/questions.json` holds 50 questions (18 easy, 16 medium, 8 hard, 8 ambiguous), each with a gold SQL query. `bench/build_expected.py` computes the expected answers from the database. The agent's final SQL is re-run and its result compared with the gold result, so grading is deterministic and free. Ambiguous questions pass only if the agent asks a clarifying question and then answers correctly.

Design choices are in [DECISIONS.md](DECISIONS.md). Prices used for every cost figure are in [docs/pricing-2026-11-02.md](docs/pricing-2026-11-02.md).

## Licence

MIT. See [LICENSE](LICENSE).
