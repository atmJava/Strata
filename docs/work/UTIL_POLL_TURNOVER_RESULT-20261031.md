# UTIL-POLL-TURNOVER result

## Decision

**UTIL-POLL-TURNOVER is rejected.** The opt-in `STRATA_POLL_FAST=1` path passed startup,
path-hit, resource and frozen greedy-parity checks, but the comparable build regressed six
and eight streams. A first candidate build was discarded because its CMake configuration
omitted performance switches used by A; B1 is retained only as a build-control failure.

The source patch was removed. A3 runs the original engine and config.

## Source and path

`Verifier::service` already returned before a CUDA call while an idle single-window stage
waited for its next mapped ring. `Verifier::batch_poll` entered `OnDevice` first, performing
`cudaGetDevice` and up to two `cudaSetDevice` calls in the two-GPU split. The candidate added
a default-off fast return for the same no-ring condition:

```text
STRATA_POLL_FAST=1
```

It still entered the unchanged path at most every 2 ms for liveness, stream query, the 20 s
timeout and normal doorbell service. It did not change row arithmetic, launch, event, commit,
sampling or finish handling. Two stage verifiers printed:

```text
strata verify: batch idle polls use the ring fast path (STRATA_POLL_FAST)
```

## Build control

The initial `build-poll` omitted A's `STRATA_MMQ_KQUANTS`, `STRATA_MMQ_TQ_OVERLAY`,
`STRATA_Q6K_EXPERTS`, external ggml and `STRATA_BUILD_TESTS` settings. Its binary was
`65,273,288 B`, 3,602,328 B larger than A, and its throughput arm B1 is not evidence about
the candidate.

The replacement `build-poll-sameflags-20261031T0835CST` matched A on Release, CUDA 75,
Ninja, ggml source, MMQ K-quants, TQ overlay, Q6_K experts, non-portable build and tests.
Its binary is `61,666,944 B`, only 4,096 B larger than A. B2 is the comparable arm.

| item | sha256 |
| --- | --- |
| A engine | `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3` |
| invalid B1 engine | `e5430bd44663c90d831363f1edb154f42792ccafadaaffe27faa706eb8e674dc` |
| comparable B2 engine | `80c0fb1cf7719021a13d5ea8efe12ed30828517cde75e34e4d50e81eefb9a844` |

## Correctness and startup

B2 loaded with six slots and two groups on both RTX 2080 Ti cards. Health reported
`loaded=true`; runtime environment contained `STRATA_POLL_FAST=1`; both stage startup markers
appeared; free VRAM was about `1007/1074 MiB`. The production systemd unit remained inactive.

The three frozen greedy probes each ran twice. B2 was repeatable, byte-identical to A1 and A3,
and every response finished with `stop`. The candidate patch itself was arithmetic-neutral.

## Throughput

The valid comparison is adjacent A2/B2/A3 on the same boot. Each arm ran 12 waves:
2/4/6/8 streams, three 400-token bursts per level.

| streams | A2 tok/s | B2 tok/s | A3 tok/s | A2/A3 median | B2 delta | paired wins |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 71.007 | 76.915 | 69.954 | 70.481 | **+9.129%** | 4/6 |
| 4 | 96.482 | 109.692 | 110.097 | 101.674 | **+7.885%** | 5/6 |
| 6 | 123.697 | 118.391 | 119.453 | 122.356 | **-3.241%** | 1/6 |
| 8 | 115.147 | 105.722 | 110.220 | 112.617 | **-6.122%** | 0/6 |

No level met both the 1% gain and 5/6 paired-win gates. Six and eight streams exceeded the 1%
non-regression limit; eight streams also lost against each adjacent A arm by `8.195%` and
`4.080%`.

The A2-to-A3 diagnostic drift was `-1.483%/+14.111%/-3.431%/-4.279%`. It exceeded 3% at three
levels, so the run also failed the drift gate. The eight-stream loss is nevertheless present
against both adjacent A measurements, and the candidate is not adopted.

All four measured arms recorded zero script errors, zero request errors and zero foreign
requests. Finish reasons were only `length` or `stop`; no `cancel`, `error` or `nonfinite`
finish appeared. B2 engine and server logs contained no execution, CUDA, never-rang or
timeout errors.

## B1 control

The invalid B1 arm measured `78.298/105.774/118.397/116.017 tok/s`. It superficially showed an
eight-stream gain, but the missing build switches make that number non-comparable. Recording
this failure prevents a later reader from treating B1 as a poll-fast result.

## Restart cost and restoration

The run required A1 to B1, B1 to A2, A2 to B2 and B2 to A3. Candidate loads took about
155-168 seconds and the final A3 load about 130 seconds, plus eight to ten seconds of stop
time per transition. No server reboot was used.

Final A3:

- server/engine/vision PID `593069/593112/593073`;
- engine sha256 `105a4171...`;
- production config sha256 `88de996a...`;
- health loaded, six slots, groups `0,1`;
- systemd inactive/dead, `MainPID=0`, `NRestarts=23`;
- RAM available about 13 GiB, swap 12 MiB, GPUs idle with about 803/908 MiB free.

## Evidence

Directory:

```text
/opt/ai/logs/util-poll-turnover-20261031T0753CST/
```

Key files:

| file | sha256 |
| --- | --- |
| `UTIL-POLL-TURNOVER-summary.json` | `2f9485dd0432f79a75d0677d91168f784d330be440d72042179d73e5cb714b9f` |
| `all-sha256.txt` | `4fdb4ad3b5b0c8fd950c74e9dbeba55ca4422dfd12daad0778760f498c3bc85d` |
| `a2-sweep.json` | `c1b47c56ed09a1e132ad2a29da89f202776b9748163c13edfa1cef2fb8cb3e77` |
| `b2-sweep.json` | `1e633e789fbc1f33f6670e295e8755a18454359d3402ce1ef3ef5faf55a0a8a5` |
| `a3-sweep.json` | `a1c6d43bfea3970aad595f160fc751a3c2f11d7b2b0c847df0632547c5bf51d9` |
| `b2-parity.json` | `4d8baea8aca7e1840aa527de800e7506bd08273d86ff33a81367e1b4859c3837` |
| `build-comparison.txt` | `dab531cb3061f0b6839881a1bdf49f8be2c7522ff8948b8ce1df098726006053` |

## Not measured

No long-context, mixed-prompt, one-hour, sampled-decoding, other model or other backend run
was made. The result covers fixed short prompts and 400-token greedy bursts on this
two-RTX-2080-Ti instance.

## Next

`UTIL-B6-ROWS` is the next exact code-path candidate. The existing
`STRATA_B6_MMVQ_ROWS=1` experiment reuses each column's activation fragment across rows for
Q4_K, Q5_K and IQ4_XS MMVQ, while preserving output bits. Upstream measurements on sm_86 and
an A4000 found `+0.5%..+2%`; this may cross the user's 1% floor for the six-row production
window. First require local kernel parity and a conservative >=1% window-cost estimate, then
run one A-B-A at 2/4/6/8 streams.
