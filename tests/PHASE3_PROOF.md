# Phase 3 random-farm proof

Reproduce with:

```sh
python -m tests.proof_run --farms 200 --seed 3000
```

This run used 200 distinct generated farms (seeds 3000 through 3199), with
2–4 tractors, 5–12 fields, 6–15 jobs, and one random tractor breakdown per
farm. Hidden speed factors were disabled so Foreman and the baseline faced the
same deterministic work durations.

| Metric | Foreman | First come |
| --- | ---: | ---: |
| Jobs completed | 1,779 | 1,566 |
| Jobs completed on time | 1,650 | 1,201 |
| Travel minutes | 26,046.6 | 27,274.6 |
| Idle minutes | 19,141.4 | 43,796.2 |
| Yard-service minutes | 21,887.9 | 31,204.7 |
| Wasted minutes | 67,076.0 | 102,276.7 |
| Rule violations | **0** | **0** |

Foreman completed at least as many jobs on time on 196 of 200 farms and used
no more wasted machine time on 187 of 200 farms. Across 400 plan dispatches,
mean re-plan time was 6.699 ms, p95 was 30.880 ms, and the maximum was
69.530 ms. The full run took 5.267 seconds on the development Mac.

These are simulator results, not measurements from physical ATC tractors.
