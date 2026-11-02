# Notes for Claude Code

- This repo is public. Don't add personal contact details (email, phone, personal websites) or client names.
- API key: read through `askdata.config.api_key()`. In cloud sessions it comes from `ASKDATA_ANTHROPIC_API_KEY`. Never set or export `ANTHROPIC_API_KEY` in the shell.
- Run `python -m pytest -q` before every commit; the tests need no API key.
- The benchmark's reference SQL (`bench/questions.yaml`) follows `data/glossary.yaml`. Never change a reference answer to make a model pass; fix the agent instead, or fix the question and its reference together if the question itself was ambiguous.
- Every API call spends money: state the expected cost before running the benchmark, and use `--budget`.
- `docs/RUNBOOK.md` has the smoke test and benchmark steps.
- Keep the README claims to what the code and the committed results show.
