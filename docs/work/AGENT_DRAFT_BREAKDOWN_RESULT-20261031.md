# AGENT-DRAFT-BREAKDOWN result

Date: 2026-10-31

Status: closed without a code candidate. The draft cost is almost entirely GPU
graph work; the removable host portion cannot reach one percent of root-task
time.

## Run

The B2 profiler-free configuration was temporarily changed by two environment
variables only:

```text
STRATA_DRAFT_TIMING=1
STRATA_MTP_PROFILE=1
```

The model, source, six slots, two groups, MTP settings, prompt and sampling were
unchanged. The profiling arm was process `76809`, engine `76816`, loaded in
151 s. B2 was restored afterward as server/vision/engine
`78965/78968/78973`, loaded in 142 s.

The profiling cost was negligible for completion span: 416 changed from
3.911 to 3.906 s, 1,490 from 13.620 to 13.605 s, and 4,483 from 37.230 to
37.018 s. The 4,483 finish sequence remains state-sensitive and is not a
mechanism result.

## Draft timing

The repeated reports converge from `2.577 ms` to `2.463 ms` per round. The
final five reports are:

| rounds | steps/round | wall | round GPU | step GPU | per step | host graph gap | tail |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 4,500 | 1.67 | 2.470 ms | 0.982 ms | 1.453 ms | 0.869 ms | 0.017 ms | 0.006 ms |
| 4,800 | 1.67 | 2.466 ms | 0.980 ms | 1.452 ms | 0.869 ms | 0.018 ms | 0.006 ms |
| 5,100 | 1.67 | 2.463 ms | 0.978 ms | 1.451 ms | 0.869 ms | 0.017 ms | 0.006 ms |
| 5,400 | 1.67 | 2.464 ms | 0.978 ms | 1.452 ms | 0.870 ms | 0.018 ms | 0.006 ms |

Host-only work is tiny per round: staging about `0.001 ms`, graph-launch calls
about `0.086–0.087 ms`, PLE prefetch `0.013 ms`, and wait/synchronization
explains the remainder of the wall time. The sum of graph work and small host
operation is effectively the measured wall. There is no hidden CPU decision
gap to remove.

The named GPU profile is stable:

- round graph about `0.93–0.98 ms`; draft-head GEMV is `0.363 ms`, the largest
  item;
- one step graph about `0.858–0.872 ms`; draft-head GEMV is `0.362 ms`,
  followed by norm/RoPE `0.068–0.073 ms`, attention `0.062–0.073 ms`,
  experts `0.051–0.053 ms`, and smaller projection/hyper-connection kernels;
- 1.67 steps per round at this request shape.

## Decision

Eliminate the host gap entirely would save about `0.02 ms/window`; even with
launch and PLE this is below 0.1% of root wall. The only large named operation
is the draft-head GEMV. If it disappeared completely, the approximate saving
would be `0.363 + 1.67 * 0.362 = 0.968 ms/window`, or about 2.2% of the
observed 43 ms decode window. At the latest trace's 37.5% model-share upper
bound this is about 0.8% of root wall, still below the 1% whole-task gate.
There is no measured host or graph-launch mechanism that can bridge the gap.

Do not scan MTP depth, `spec-min-p`, groups, cache knobs or tree variants. Do
not implement a head-only Q4 change on this evidence: its hypothetical full
elimination is already below the whole-task gate, and it would alter draft
acceptance/output quality. `AGENT-DRAFT-BREAKDOWN` is closed.

## Evidence

Directory:
`/opt/ai/logs/agent-profile-hygiene-20261031T082551Z/`.

- `p-draft-config.json` SHA-256
  `3e64408704b161e84aeca24ed231e0b36c878be2e34df50cc2e6b969ffb7a5c2`
- `p-draft/decode-416.json` SHA-256
  `30ec9c9dc45e4481367a9457a3052b71da933cc78267b31f83bb5aa3ad6f999c`
- `p-draft/decode-1490.json` SHA-256
  `2fffd0d1ad9baebea2400df518828682637ba81f941abeede50ac4c1fdec84ec`
- `p-draft/decode-4483.json` SHA-256
  `e9ef1eccd991e8afe9b75dfa0671c2a319ad5667c052fbc74e641064badc2f4f`
- `p-draft/profile-lines.txt` SHA-256
  `5397685d7f947280d5655008b866c108cec207c8da50ca6fe081698e85c29776`

No code was built or deployed. No production default changed. The final B2
engine environment contains neither profiling pair.
