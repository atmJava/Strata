# AGENT-HOT-TURN-FIXED-COST contract

Date: 2026-10-31

Status: rejected after A64/B0/A64r measurement. See
[`AGENT_HOT_TURN_FIXED_COST_RESULT-20261031.md`](AGENT_HOT_TURN_FIXED_COST_RESULT-20261031.md).

## Question

On a normal 1- or 2-conversation Codex-like hot continuation, does the default
`--short-read 64` path make the final short text segment pay an avoidable fixed
wait through decode windows?

The measured hot turn is about 12K prompt tokens, reuses about 95%, and spends
`1.4-1.5 s` in prompt processing while short decode takes only `50-73 ms`.
A real engine log also contains `11,857 reused + 596 read` with a `~1.5 s`
prompt. Static control-flow reading records:

- `--short-read` defaults to 64 fresh text tokens;
- the batched prompt path has roughly `300 ms` fixed cost even for a small run;
- the decode-window path is roughly `16 ms/token`;
- the crossover is therefore plausibly tens of tokens, not necessarily 64.

This contract tests the existing mechanism only. It does not change kernels,
model arithmetic, sampling, tool handling or finish rules.

## Production and privacy

- Use the active Qwen3.8 IQ3_XXS model, the current engine binary and the
  accepted six-slot/two-group config.
- Static control-flow check found that `STRATA_PIPELINE_SWITCH` is read only
  inside the `pipeline-windows` branch. The accepted production shape uses
  `--batch 6`; that path explicitly disables `--pipeline-windows`, so the
  dynamic switch is unreachable and must not be claimed as a valid A/B.
- Do not write an API key, prompt, assistant text, reasoning text or tool body
  into the repository or result files.
- Keep the manual production instance and systemd unit state unchanged except
  for the declared temporary experiment boot.
- Record server cwd, engine path, binary/config hashes, PIDs, health, slots,
  groups and GPU state before and after.

## Arms and fixed workload

Same engine binary and accepted production config, with one deliberate change
between the two candidate arms:

- `A64`: current running process with `short_read=64` (the default);
- `B0`: fresh manual boot with explicit `--short-read 0`;
- `A64r`: fresh manual boot restoring the exact A64 config, to measure
  cross-boot drift.

Each arm uses deterministic, temperature-zero conversations with the current
model and at least 12K tokens of shared/history context. Run at least three
waves at concurrency 1 and at concurrency 2. Each wave has:

1. a cold first turn;
2. a hot continuation with a short new tool/result header;
3. a hot continuation close to the real `~596` fresh-token shape.

Replies must contain exact sentinels or exact expected tokens so output text can
be compared without storing prompts. Include at least one syntax-highlighted
code fragment containing `</script>` and apostrophes to exercise dialogue
rendering, but compare only fixed hash/exit metadata in committed results.

After the hot-turn comparison, run a 4-conversation non-regression sweep in
each arm.
Concurrency 6/8 is outside this candidate because it does not use the ordinary
1/2-conversation edit-test shape; the existing safety suite remains unchanged.

## Measurements

For every request record:

- arm, concurrency, wave and turn class;
- prompt, cached and uncached token counts;
- server prompt, read, decode and client wall/TTFT/slot-wait times;
- output count, finish, exact reply/hash and HTTP/engine errors;
- request counts and foreign-request count.

For each arm aggregate medians and paired changes. Keep raw private outputs
outside the repository and record only hashes/verdict metadata there.

## Decision gate

Candidate `B0` may replace `A64` only when all hold:

- at least one ordinary hot-turn class improves median wall and server prompt
  time by at least 1%, with absolute saving at least `0.15 s`;
- the other hot-turn classes at concurrency 1/2 do not regress by more than 1%;
- `A64r` server-prompt drift against `A64` is at most 3%;
- exact replies/finish/cache shapes match, with zero HTTP or engine errors;
- 4-conversation wall or aggregate throughput does not regress by more than 1%;
- health loaded, 262144 context, 6 slots, 2 groups and safe RAM/VRAM/swap hold;
- no testing-only switch or temporary instrumentation is present in the
  adopted production path.

If `B0` passes, adopt `--short-read 0` as an explicit production argument and
record a one-argument rollback to `64`. If it fails, restore the current config
and do not scan nearby thresholds unless the measured curve gives a specific
new mechanism and a conservative 1% upper bound at another size.

The candidate is rejected if the gain exists only in a synthetic prompt that
does not exercise the ordinary agent continuation path.

## Restart budget

The active process cannot use the dynamic switch because production has batch
slots. The experiment therefore allows two temporary manual boots: one for
`B0`, and one for `A64r`. Each cold load has recently measured 128–180 seconds;
the expected recurring saving is hundreds of milliseconds per ordinary hot
turn, so this total restart cost is accepted only once for the decision and
must not expand into nearby thresholds.

If the engine cannot start within 5 minutes, or health/6 slots/2 groups do not
match, stop and restore the current config instead of debugging adjacent GPU
settings. After the experiment, either adopt the passed one-argument config or
restore the exact current A and verify health.
