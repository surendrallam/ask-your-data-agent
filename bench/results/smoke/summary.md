| Config | Effort | Accuracy (all) | Answer accuracy | Clarify accuracy | $ / question | Total $ | p50 latency | p90 latency | API calls / q | Escalated |
|---|---|---|---|---|---|---|---|---|---|---|
| Haiku 5.5 | medium | 100.0% | 100.0% | 100.0% | $0.0008 | $0.00 | 4.3s | 13.6s | 2.0 | – |
| Sonnet 5.5 | medium | 100.0% | 100.0% | 100.0% | $0.0123 | $0.06 | 4.8s | 8.0s | 2.0 | – |
| Opus 5.5 | medium | 100.0% | 100.0% | 100.0% | $0.0263 | $0.13 | 7.0s | 9.5s | 2.0 | – |
| Auto (Haiku → Sonnet) | medium | 100.0% | 100.0% | 100.0% | $0.0006 | $0.00 | 3.8s | 6.5s | 2.0 | 0.0% |

| Question | haiku/medium | sonnet/medium | opus/medium | auto/medium |
|---|---|---|---|---|
| c01 | ✓ | ✓ | ✓ | ✓ |
| q01 | ✓ | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ | ✓ |
| q25 | ✓ | ✓ | ✓ | ✓ |
| q30 | ✓ | ✓ | ✓ | ✓ |
