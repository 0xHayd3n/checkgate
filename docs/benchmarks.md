# Pilot measurements

These measurements describe Checkgate's controlled Python host on small synthetic coding tasks. They do not measure native Hermes or DS Bot, and do not establish savings on arbitrary projects.

## Design

The 12 November 2026 experiment ran six Python bug-fix tasks, two trials per task, and four host variants: 48 serial runs. A deterministic Williams counterbalanced order reduced order bias. Each arm received its own fresh fixture. All arms used completion control, identical required checks, model/backend settings and budgets. Variants changed only initial editable-source preloading and task-brief compaction.

The backend was the locally installed Codex CLI using its existing ChatGPT sign-in, model `gpt-6.1-sol`, low reasoning. There was no API fallback. A separate acceptance evaluator checked each resulting artifact. All 48 completed artifacts passed; all usage records were available. This is a small pilot with two observations per task/variant, not a statistical performance guarantee.

## Results

Each row contains 12 runs. Tokens = reported input + reported output. Cached input is already included in input and must not be added again.

| Variant | Total tokens | Token reduction | Mean task wall | Wall reduction | Model rounds | Check executions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Existing controlled host | 282,721 | reference | 23.31 s | reference | 24 | 24 |
| Compact brief only | 274,056 | 3.1% | 22.37 s | 4.0% | 24 | 24 |
| Source preload only | 144,760 | 48.8% | 15.32 s | 34.3% | 12 | 24 |
| Source preload + compact brief | 153,903 | 45.6% | 16.89 s | 27.5% | 13 | 26 |

Source preloading alone was the best observed arm. One combined-arm HTTP retry fix failed its first check and needed another model round; the repair and repeated checks are included. This single outcome does not prove brief compaction caused the failure. All final independent acceptance checks passed. No required verification was removed.

Task wall time includes fixture setup, host work and artifact/evaluation overhead. The operator's serial experiment wall was **936.10 s (15 min 36 s)**, including checkpoint/cleanup overhead; it is not the sum of model decoding durations. Native CLI context and caching influence usage. These token reductions are neither dollar savings nor a measurement of subscription quota.

## Effective TPS

| Variant | Output tokens / model-call elapsed seconds | Output tokens / task wall seconds |
| --- | ---: | ---: |
| Existing controlled host | 17.91 | 17.56 |
| Compact brief only | 17.63 | 17.26 |
| Source preload only | 25.14 | 24.40 |
| Both | 24.62 | 23.93 |

These are effective output throughput ratios. Model-call elapsed time includes CLI startup, request latency, input processing and generation; it is **not pure decoding TPS**. Input + output tokens divided by wall time is a different metric and should not be called generation speed.

Sanitized numeric totals are in [pilot-summary.json](pilot-summary.json). Raw prompts, transcripts, local paths, evidence and task artifacts are excluded from this public release. Older baseline-versus-controller experiments use a different comparison; their percentages are not additive with this optimization test.

## Re-run

Unit and scripted tests do not use a model:

```sh
python -B -m unittest discover -v
python -B entrypoint.py demo --output results/demo
```

The optional live runner requires a separately installed and signed-in Codex CLI. First run its preflight without `--live`:

```sh
python -B optimization_benchmark.py
python -B optimization_benchmark.py --live --trials 2 --output results/my-pilot
```

The second command makes real model calls and consumes the account's usage. The adapter rejects an API-key login and has no API fallback. Consult the installed CLI's current account/usage terms yourself. Output folders may contain source excerpts, provider output and local paths; the default `results/` directory is ignored by Git. Review any custom output before sharing it.

The release adds packaging and a Hermes tool-name compatibility fix to the measured host implementation. Re-running on another machine, model, CLI version or workload can produce different results. For a deployment comparison, measure that app's original loop against the integrated loop with identical tasks, checks, model settings and independent acceptance criteria.
