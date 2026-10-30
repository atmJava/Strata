# UTIL-BATCH-GROUP-FIT contract

Status: pre-registered before implementation.

## Evidence and selected problem

The current A shape uses six slots in two pipelined groups. At two active streams, each group often
has one active slot, so the two-stage pipeline carries one row per group and pays the hand-off between
cards. The current A3 baseline measured `81.0 tok/s` at two streams, while 4 and 6 streams reached
`116.1` and `133.2 tok/s`. Historical runs showed one group can be slower at high concurrency, but
the current two-stream crossover has not been isolated with the current binary and model.

This experiment asks one question: **does one serial group beat two pipelined groups at two streams
without harming four, six or eight streams?** It does not add drafts and does not scan other
scheduling parameters.

## Arms

A is the restored baseline config:
`/opt/ai/logs/qsa-diag-20261031T1653Z/a-baseline.json`
(sha256 `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`).

B copies A and changes exactly one argument:

```text
--batch-groups 2 -> --batch-groups 1
```

Every environment entry and every other argument stays unchanged. B must start with six slots, one
group, the same `17274` expert slots within measurement tolerance, health loaded and engine sha256
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.

## Frozen workload

1. On restored A, run 8 streams for three repetitions with `tools/server/bench-strata.py`. The 2/4/6
   A3 baseline already exists and is reused.
2. Start B and run the same frozen three fixed greedy probes twice. Both B trials must be repeatable
   and equal to the recorded A text with the same finish reason before performance work.
3. If parity passes, run 2/4/6/8 streams for three repetitions and then restore A4 and run the same
   four levels.

All benchmark requests use the existing fixed prompts and 400 requested output tokens. Record
aggregate tok/s, wall, TTFT, slot wait, decode rate, draft acceptance, request counts and resources.

## Gate

Adoption requires:

1. at two streams, B aggregate is at least `1%` above the A3/A4 combined median and wins at least
   `5/6` paired bursts;
2. at 4, 6 and 8 streams, B medians are no more than `1%` below A3/A4;
3. zero request errors, zero unexpected finish reasons, zero foreign requests;
4. six slots, health, RAM, VRAM and swap stay within the current safety envelope;
5. production systemd and its files remain unchanged.

If one group improves two streams but violates the high-concurrency gate, reject the static B
configuration and use the measured crossover as evidence for one separate concurrency-aware
scheduling target. Do not scan group counts beyond 1 and 2.

## Reject gate

Restore A and record a rejection if startup loses a slot or silently falls back, any parity probe
fails, the two-stream gain is below 1%, any 4/6/8-stream median regresses by more than 1%, or health,
resource or request integrity fails.

## Restart cost

The candidate requires one A-to-B start and one B-to-A restore, about 3-4 minutes each. The existing
A3 workload and current 2/4/6 data justify this single schedule comparison because it targets a
repeatable user-visible low-concurrency trough and may be adoptable as configuration without code.

