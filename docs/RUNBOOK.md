# Runbook: smoke test and benchmark

For running the benchmark in a fresh environment (a cloud Claude Code session or a laptop).

## 0. The API key

- **Laptop:** put `ANTHROPIC_API_KEY=...` in this repo's `.env`. Never export it in the shell.
- **Cloud session:** the environment provides `ASKDATA_ANTHROPIC_API_KEY`. Never set `ANTHROPIC_API_KEY` there: Claude Code would use it for its own requests.

Check that it is found:

```bash
python -c "from askdata.config import api_key; print('key found' if api_key() else 'NO KEY')"
```

## 1. Set up

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m askdata.seed
.venv/bin/python -m pytest -q
.venv/bin/python -m bench.run --dry-run | tail -1      # expect: 50 questions, 0 problems
```

## 2. Smoke test (about $0.50, 2-3 minutes)

```bash
.venv/bin/python -m bench.run --configs haiku,sonnet,opus,auto --only q01,q17,q25,q30,c01 --tag smoke --budget 3
```

Check `bench/results/smoke/results.jsonl`:

- every run has `status` answered or clarify, none `failed`;
- `cache_hit_ratio` is above 0 from the second API call on, which shows caching works;
- Sonnet and Opus runs show no fallback errors.

Fix real problems in the agent, prompt or tools. **Never edit a reference answer to make a model pass.**

## 3. Full benchmark (about $10-20, 20-40 minutes at concurrency 4)

```bash
.venv/bin/python -m bench.run --configs haiku,sonnet,opus,auto --concurrency 4 --budget 40 --tag 2026-11-03
```

If it stops (budget, network), run the same command again: it resumes.

## 4. With the remaining credits

In order of value:

1. A second repetition to measure consistency: `--repeat 2` with a new `--tag` (it runs everything again).
2. An effort sweep: `--configs haiku,sonnet --effort low,high --tag effort-sweep`.

## 5. Publish

1. Copy the first table from `bench/results/<tag>/summary.md` into the README's results section with one line on the method and the date.
2. Read the misses in `results.jsonl` (`passed: false`) and summarize the patterns in two or three bullets.
3. Commit `bench/results/<tag>/` (results.jsonl, summary.json, summary.md).
