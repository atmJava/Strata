# Strata live three-session stall and review fix

Date: 2026-11-01, Asia/Shanghai.

## Live observation

Production remained on the unchanged service while three child-agent sessions
were active. No restart was taken.

At `03:16:08`, three requests were running: two slots decoded while one read a
`160,290`-token prompt. The `/metrics` sample then showed:

| time | live state | slot output |
|---|---|---|
| `03:16:12` | 3 running, 1 reading | slot 0 generated 589; slot 3 generated 132 |
| `03:16:16`-`03:17:00` | 3 running, 1 reading | slots 0 and 3 generated no new token |
| `03:17:04` | 1 outside slot, all six slots idle | request boundary |
| `03:17:08`-`03:18:32` | 3 running, 1 reading | slot 0 generated no new token |
| `03:18:36` | 3 running, two decoding | the read advanced to work |

The longest observed uninterrupted decode gap was about 80 seconds. The next
read began at `03:18:36`, and the same two decoding slots immediately became
stalled again. This is much larger than the roughly 1.6-second concurrent-read
lead-in in the engine log.

The production log confirms the common shape:

```text
prompt 160290 tokens = 0 reused + 160290 read in 67389 ms
prompt 175043 tokens = 0 reused + 175043 read in 88360 ms
the prompt was read in 2 parts, the slots decoding 1621 ms between them
```

It also repeatedly reports:

```text
conversation cache: slot N not parked before its overwrite
(... tokens have no per-slot draft K/V without --batch-mtp)
```

and physical-RAM admission failures. The live evidence therefore keeps the two
large structural targets:

1. A cold 160K-175K prompt occupies the engine long enough to freeze decode
   neighbors for tens of seconds.
2. Some three-session turns cannot park the conversation at their slot
   boundary, so the next request starts with zero reuse.

Restarting production would not address either target and would discard the
current conversation cache. No restart was made.

## Review follow-up

The remaining low-risk review issues were fixed without touching the running
service:

- A `STRATA_TAP_FILE` path without `%d` on a layer-split stage still emits the
  explanatory warning, but now allocates no dump-only tap, scratch or mapped host
  buffers and leaves `tap_on_` false. DFlash2's independently requested taps
  remain enabled.
- The CLI's two DFlash2 fallback paths now call `release()`, matching serve and
  the other failure paths.
- `PORT_AUDIT_COVERAGE.md` now records the measured `17 passed` for tap parity,
  not the stale 14.

## Validation

- `tap_dump_plan_test`: passed with
  `c++ -std=c++20 -Wall -Wextra -Werror`.
- The production host configured the copied source with CUDA 13.0/sm_75 and
  compiled `src/core/verify.cpp` and `src/program/generate.cpp` separately with
  the exported compile commands, at `nice -n 19`: both returned 0. The test also
  passed through CMake/CTest on that host.
- `python -m pytest tools/server/tests/test_tap_parity.py -q`: `17 passed`.
- `git diff --check`: passed.
- The local workstation has no `nvcc`; the CUDA object check was therefore run
  only on the remote temporary copy. No binary was linked or deployed, so the
  running engine remains unchanged.

## Next priority

Do not restart for the review fix: it affects only an opt-in diagnostic/fallback
path and has no measured effect on the current production bottleneck. Before
any production candidate, pre-register the three-session `160K-200K` shape and
compare:

- longest neighbor no-token stall;
- each request's prompt read and decode wall;
- whole-task wall;
- zero-reuse count and park/restore outcomes;
- output, finish reason and tool behavior.

The first structural candidate remains a safe independent prompt staging source
that actually enters the concurrent reader with the staged expert source. A
conversation-cache restore that does not require per-slot draft K/V is the next
large target if it can preserve exact state.
