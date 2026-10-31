# AGENT-REAL-DECODE-SHAPE contract

Date: 2026-10-31

Status: pre-registered before the live shape replay.

## Question

Where does model decode time go in the real local Codex workload, and which
mechanism has a conservative whole-task bound of at least 1% without changing
code, tool choice, test results or finish semantics?

This phase is diagnostic. It does not change the server, engine, configuration
or default sampling.

## Evidence already fixed

The complete local rollout scan covers 35 files and 152 roots. Model spans,
which include client-visible waiting around the request, total
`69,236.197 s` union against `184,604 s` root-wall union. Output and reasoning
totals are `10,343,967` and `3,347,547` tokens.

Output percentiles are `416 / 1,490 / 4,483` tokens at p50 / p90 / p99.
Reasoning percentiles are `48 / 631 / 2,422`.

By output size:

| output tokens | requests | model-span share |
|---|---:|---:|
| 0-127 | 19.698% | 7.336% |
| 128-511 | 36.809% | 20.611% |
| 512-2047 | 38.231% | 49.225% |
| 2048+ | 5.261% | 22.828% |

By reasoning size, requests with at least 512 reasoning tokens are `12.006%`
of requests but `31.910%` of model-span seconds. By the action following the
request, `exec_command` requests are `63.541%` of model-span seconds, edits are
`15.735%`, polling is `12.298%`, and compaction is `4.580%`.

The same trace has only `171.303 s` of overlapping two-request model time and
`69,064.894 s` at one active request. Decode optimization therefore has a much
larger potential reach than two-stream role scheduling.

These are client event spans, not Strata `prompt_ms`, `decode_ms` or TTFT.

## Live replay

Use the current B4 production instance without restarting it:

- model: `qwen3.8-flash-next-iq3_xxs-abliterated`;
- engine `0.1.51`, MTP on, `spec=8`, `mtp-max=4`;
- 6 slots, 2 groups, context 262144;
- current `STRATA_BATCH_SERIAL_TWO=1` admission;
- same warm prompt, temperature 0, reasoning effort `none`.

Run three output sizes: `416`, `1,490` and `4,483` tokens. At each size run
three waves at one and two concurrent requests. The prompt and requested output
limit stay fixed within each pair. Save per-request prompt/decode milliseconds,
TTFT, slot wait, wall, completion tokens, finish and output hash, plus the
wave completion span and aggregate throughput.

## Acceptance of the diagnostic

The shape diagnostic is complete only if:

- there are no HTTP or execution errors;
- all expected requests return exactly the requested output token count and
  `finish=length`;
- one and two stream results are reported separately;
- the second request's TTFT and the completion span are reported rather than
  hiding serial admission behind aggregate decode time;
- the result distinguishes client model-span estimates from Strata server
  timings and does not generalize beyond this model and prompt class.

## Mechanism gate

No mechanism is implemented until a conservative whole-task calculation uses
the real trigger frequency and satisfies all of:

1. At least 1% reduction in root-task wall time, not merely token/s or one
   decode phase.
2. The affected request classes are present in the real trace.
3. Code edits, tool calls, tests and finish semantics remain correct.
4. One stream does not regress; two streams report both second-request TTFT and
   completion span; four streams do not regress by more than 1%.
5. The default path stays byte-identical, and the candidate is opt-in and
   reversible.
6. Any restart cost is lower than the expected saving.

Reducing reasoning or output length by silently changing user intent is not a
valid mechanism.

## Stop rules

Stop the live replay early on an HTTP error, unexpected finish, resource
warning, or more than about 20% deviation in requested output length. Do not
scan speculative depth, thresholds, groups or slot counts again; those axes
were already rejected and this phase is not a parameter sweep.

Do not treat two-stream serial completion as throughput improvement merely
because decode timers overlap less. The user-visible completion span and second
TTFT are the decision fields.
