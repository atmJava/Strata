# UTIL-PIPED-MTP contract

Status: rejected by the quality and high-concurrency gates; see
[`UTIL_PIPED_MTP_RESULT-20261031.md`](UTIL_PIPED_MTP_RESULT-20261031.md).

## Evidence and selected problem

The current production candidate uses six slots, two pipelined groups and `--mtp`, but does not
enable `--batch-mtp`. The pipelined path therefore verifies one ordinary token row per active
slot and does not use the slots' MTP proposals. The v0.2.7 code already contains
`STRATA_PIPED_MTP`, which keeps the pipeline and drafts inside each group.

The engine documentation records measured gains for this existing mechanism on a two-GPU box:
`+16.0%` at two streams with one proposal, `+10.2%` at four streams with one proposal, with exact
per-slot output in the checked batch protocol. The current 2080 Ti shape has not been measured.
It differs in VRAM headroom, expert residency and six slots, so the documented gain is a reason to
test, not an adoption claim.

This is a structural scheduling candidate rather than another parameter sweep. It directly
addresses the measured low-concurrency aggregate:

```text
2 streams 94.85 tok/s, below the 104.05 tok/s single stream
4 streams 139.92 tok/s
6 streams 164.68 tok/s
```

## Candidate

Create a separate candidate config from
`/opt/ai/logs/qsa-diag-20261031T1653Z/a-baseline.json` without changing the running config file.

- append `--batch-mtp` to the engine args;
- set `STRATA_PIPED_MTP=1`;
- set `STRATA_BATCH_SELF_COMMIT=0`, because pipelined MTP is explicitly disabled with self commit;
- keep `--batch 6`, `--batch-groups 2`, `--mtp`, `--spec 8`, layer split, context, KV, expert-cache
  and reserve settings unchanged;
- use the existing engine binary; production systemd, production config and its snapshots remain
  untouched.

The candidate config passes only if its startup says all of:

- `--batch-mtp: 6 slot drafters`;
- `STRATA_PIPED_MTP: 2 pipelined groups of 3 slots draft up to 1 MTP proposal per slot each
  window`;
- `/metrics` reports `batch_slots=6` and `batch_groups=2`;
- health is loaded and the actual engine hash is
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.

If the drafters do not fit, fewer than six slots survive, the mechanism falls back silently, or
startup requires changing more than one reserve/expert-cache control, reject this candidate and
restore the A config.

## Frozen workload

Use the existing `tools/server/bench-strata.py` HTTP workload:

- `BENCH_N=2`, `BENCH_N=4`, `BENCH_N=6`;
- three repetitions per concurrency;
- ordinary production candidate on port 8080;
- fixed prompts and `MAX_TOKENS=400` already defined by the tool;
- no unrelated requests during a burst.

Run A1 on the current service, stop it once, start B from the candidate config, run the same three
concurrency shapes, then restore and start A2 and run them again. Use per-run medians and preserve
the three raw repetitions.

## Keep gate

All conditions must pass:

1. Candidate startup and `/metrics` prove six slots, two groups and pipelined MTP are active;
2. at two streams, B aggregate throughput is at least `1%` above the combined A1/A2 median and B
   wins at least `5/6` paired burst comparisons;
3. four and six stream aggregate medians are no more than `1%` below their combined A medians;
4. every request succeeds, no unexpected `finish_reason=length` appears, and fixed greedy outputs
   match the pre-candidate baseline for the parity probes;
5. six slots remain available and health stays loaded throughout;
6. after the A2 restore, VRAM free, RAM available and swap remain within the existing safety limits,
   and no production systemd/config file changed.

Passing permits one longer mixed-traffic soak and then adoption of the candidate config. A
sub-one-percent change is rejected even if correct.

## Reject gate

Reject and restore the A config if any of these occurs:

```text
candidate does not start or loses slots
pipelined MTP log marker is absent
two-stream improvement is below 1%
four- or six-stream median regresses by more than 1%
request errors, output mismatch or unexpected length
RAM/VRAM safety margin is lost
```

Do not scan `STRATA_PIPED_MTP=2/3`, batch-group counts, slot counts or nearby concurrency after a
failed six-slot two-group result. `=1` is the only setting whose 3-slot group window fits the
eight-row verifier limit at `6 / 2` slots per group.

## Baselines

- engine `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- candidate config (A) sha256
  `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`;
- 1/2/4/6/8-stream aggregate from L-092:
  `104.05/94.85/139.92/164.68/138.76 tok/s`;
- current idle VRAM free `1007/1074 MiB`, available RAM about `14.2 GiB`, swap not used.

## Restart cost

A1 is already loaded. A-B-A requires two candidate transitions, each about three minutes, plus the
benchmark time. This cost is justified only because existing v0.2.7 measurements show a
double-digit two-stream opportunity and the current service has a measured throughput trough.
