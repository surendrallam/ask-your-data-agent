# Field notes

What 1,700 graded runs of this agent showed, written for anyone deciding whether to trust an AI data assistant. Every number comes from the committed results in [`bench/results/`](bench/results/); the [README](README.md#results) has the full tables.

## How the agent is graded

- **The test set:** 50 questions about a synthetic sales database. 45 are answerable; 5 should get a clarifying question, such as "Apex", which is both a customer and a product brand.
- **Reference answers:** each answerable question has reference SQL, written against the metric definitions in [`data/glossary.yaml`](data/glossary.yaml).
- **Grading by execution:** the SQL the agent cites is re-run and its rows are compared with the reference rows.
  - Counts must match exactly, money within 0.01%, and rates within 0.06 percentage points.
  - Extra columns are fine.
  - Asking a clarifying question on an answerable question counts as a miss.
- **Runs:** every question runs 3 times per setting, so each setting is graded on 150 runs.

## Four things the runs showed

1. **Most early misses were the right number in the wrong-shaped answer.** In the first run, 7 of the 9 misses had the correct figure inside a wider table. One added instruction, "finish with a query that is exactly the answer", took Sonnet 5.5 from 94% to 100% and Opus 5.5 from 98% to 100%.
2. **The smallest model's misses were systematic, not random.** Asked for "the sales trend for Apex", Haiku 5.5 picked the brand in all six of its runs (three as Haiku, three in auto mode) instead of asking which Apex was meant.
3. **Self-reported confidence was no use for routing.** Haiku never rated itself low-confidence, even when it was wrong, so the confidence-based escalation to Sonnet never fired. A targeted check, such as "does this name match more than one kind of entity?", would catch the main miss more cheaply than a bigger model.
4. **When thinking is cut, the clarifying question goes first.** At low effort, Sonnet 5.5 missed exactly one question: it stopped asking which Apex was meant.

## Cost per correct answer

Cost per 1,000 correct answers is the setting's total API cost divided by its correct runs, times 1,000. It counts what a right answer costs, not just a call.

| Model and effort | Accuracy (150 runs) | $ per 1,000 questions | $ per 1,000 correct answers | Median latency |
|---|---|---|---|---|
| Haiku 5.5, low | 96.0% | $0.55 | $0.57 | 4.2 s |
| Haiku 5.5, medium | 96.7% | $0.67 | $0.69 | 4.7 s |
| Haiku 5.5, high | 98.0% | $0.79 | $0.80 | 5.2 s |
| Auto (Haiku first, Sonnet if needed), medium | 97.3% | $0.64 | $0.65 | 4.2 s |
| Sonnet 5.5, low | 98.0% | $8.05 | $8.21 | 4.4 s |
| Sonnet 5.5, medium | 100% | $8.34 | $8.34 | 4.5 s |
| Sonnet 5.5, high | 100% | $9.04 | $9.04 | 4.9 s |
| Opus 5.5, low | 100% | $17.43 | $17.43 | 6.5 s |
| Opus 5.5, medium | 100% | $19.70 | $19.70 | 7.2 s |
| Opus 5.5, high | 100% | $20.35 | $20.35 | 7.1 s |

- **Haiku 5.5 at high effort matched Sonnet 5.5 at low effort,** 98.0% each, for $0.80 against $8.21 per 1,000 correct answers: about a tenth of the cost.
- **The cheapest setting with no misses was Sonnet 5.5 at medium effort,** at $8.34 per 1,000 correct answers.
- **Opus 5.5 had no misses at any effort,** at 2.1–2.4 times the cost of Sonnet 5.5 at medium effort.

## How much to trust these numbers

- **Small samples.** Each setting ran 150 times on one synthetic dataset. A difference of 1–2 points is one to three runs, which is within noise.
- **Costs** are Anthropic's list prices applied to the recorded token counts. Sonnet 5.5's cache-read price was corrected on 3 Nov 2026 ([`DECISIONS.md`](DECISIONS.md)).
- **Latency** is wall-clock time from one machine, so it varies with load.

## What this means for a production data assistant

- **Write the test set before tuning the prompt:** real questions with reference SQL, graded by running the SQL, not by reading the wording.
- **Compare models on cost per correct answer.** Here a smaller model at higher effort matched a larger one at a tenth of the cost.
- **Check for ambiguous names before answering.** On this workload, such a check would have caught the main miss more cheaply than a bigger model.
