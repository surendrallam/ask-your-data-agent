# Benchmark results

Runs: bench-20261102T062455Z

| Model | Accuracy | Strict grader | Easy | Medium | Hard | Ambiguous | Cost / question | Median latency | Total cost |
|---|---|---|---|---|---|---|---|---|---|
| Haiku 5.5 | **46/50 (92%)** | 44/50 | 18/18 | 16/16 | 8/8 | 4/8 | $0.0005 | 3.8 s | $0.03 |
| Sonnet 5.5 | **46/50 (92%)** | 46/50 | 18/18 | 16/16 | 8/8 | 4/8 | $0.0085 | 5.0 s | $0.42 |
| Opus 5.5 | **49/50 (98%)** | 49/50 | 18/18 | 16/16 | 8/8 | 7/8 | $0.0175 | 6.2 s | $0.87 |

| Model | Asked when ambiguous | Asked when not needed | Mean API calls | Mean output tokens | Prompt tokens read from cache | p90 latency | Escalated | Errors |
|---|---|---|---|---|---|---|---|---|
| Haiku 5.5 | 4/8 | 0/42 | 2.6 | 664 | 91% | 5.9 s | 0 | 0 |
| Sonnet 5.5 | 4/8 | 0/42 | 2.7 | 546 | 91% | 7.2 s | 0 | 0 |
| Opus 5.5 | 7/8 | 0/42 | 2.8 | 588 | 92% | 11.1 s | 0 | 0 |

## Misses

| Model | Question | Corrected grader | Strict grader |
|---|---|---|---|
| Haiku 5.5 | A02: Which genre performs best? | did not ask a clarifying question (guess matched) | did not ask a clarifying question (guess matched) |
| Haiku 5.5 | A04: Who is the best sales rep? | did not ask a clarifying question (guess matched) | did not ask a clarifying question (guess matched) |
| Haiku 5.5 | A07: Which country is our top market? | did not ask a clarifying question (guess matched) | did not ask a clarifying question (guess matched) |
| Haiku 5.5 | A08: How are we doing in Europe? | did not ask a clarifying question (guess matched) | did not ask a clarifying question |
| Haiku 5.5 | E12: How many video tracks are in the catalog? | correct | expected 214 in first row |
| Haiku 5.5 | E17: How many customers does each sales rep support? | correct | expected 3 rows, got 8 |
| Opus 5.5 | A04: Who is the best sales rep? | did not ask a clarifying question (guess matched) | did not ask a clarifying question (guess matched) |
| Sonnet 5.5 | A04: Who is the best sales rep? | did not ask a clarifying question (guess matched) | did not ask a clarifying question (guess matched) |
| Sonnet 5.5 | A06: How did sales change? | did not ask a clarifying question (guess matched) | did not ask a clarifying question (guess matched) |
| Sonnet 5.5 | A07: Which country is our top market? | did not ask a clarifying question (guess matched) | did not ask a clarifying question (guess matched) |
| Sonnet 5.5 | A08: How are we doing in Europe? | did not ask a clarifying question (guess matched) | did not ask a clarifying question |
