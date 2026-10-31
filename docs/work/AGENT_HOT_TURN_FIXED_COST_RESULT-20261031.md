# AGENT-HOT-TURN-FIXED-COST result

Date: 2026-10-31

Status: rejected. Keep the default `short_read=64`; no production argument or
source change was adopted.

## Decision

`short_read=0` did not reduce the fixed cost of ordinary agent hot turns. It
made every tested hot-turn class slower or neutral:

| concurrency | turn | fresh shape | A64 prompt/wall | B0 prompt/wall | B0 change |
|---:|---|---|---:|---:|---:|
| 1 | hot short | about 76 prompt tokens | 458.0 ms / 0.596 s | 520.8 ms / 0.643 s | -13.75% / -8.14% |
| 1 | hot long | about 959 prompt tokens | 1418.2 ms / 1.533 s | 1471.0 ms / 1.585 s | -3.96% / -3.55% |
| 2 | hot short | about 76 prompt tokens | 766.9 ms / 1.814 s | 767.0 ms / 1.822 s | -3.10% / -3.07%* |
| 2 | hot long | about 959 prompt tokens | 1625.2 ms / 3.534 s | 1665.9 ms / 3.623 s | -2.71% / -2.72% |
| 4 | hot short | about 76 prompt tokens | 617.7 ms / 3.224 s | 689.5 ms / 3.268 s | -9.41% / -3.86%* |
| 4 | hot long | about 959 prompt tokens | 1604.3 ms / 6.876 s | 1635.7 ms / 7.094 s | -3.24% / -3.45% |

The level-2 and level-4 percentages at the starred cells use the A64/A64r
reference median. Their A64r drift was above 3%, so those exact percentages are
not used for fine attribution. B0 still won no hot-turn class and showed the
same direction in every class; the candidate fails even before relying on
sub-3% cross-boot differences.

The 1-concurrency A/A pair was stable: hot-short prompt drift was 0.066% and
hot-long drift was 0.458%. The large B0 regression there is therefore not
explained by boot noise.

## Workload and quality

Each arm sent 63 requests:

- concurrency 1/2/4;
- three waves per concurrency;
- cold 16.9K-token turn, short hot continuation, and long hot continuation;
- temperature zero, `max_tokens=8`, exact sentinels, and a rendering fixture
  containing `</script>` and an apostrophe.

All three arms had zero HTTP/engine errors, zero foreign requests, `finish=stop`,
and all exact replies matched. Cold prompts cached `0`; hot prompts reused
about `99.5%`. No prompt, response, reasoning, tool body or API key was stored
in the result.

The intended "12K" context measured 16,900 prompt tokens because the synthetic
word template maps to more tokens than one per word. This does not change the
decision: it remains the ordinary hot-continuation shape, and B0's regression
is strongest at concurrency 1.

## A/A drift

Most A64/A64r classes were below 3%, but short-read classes at concurrency 2/4
exceeded it:

| class | prompt drift | wall drift |
|---|---:|---:|
| level 2 hot short | 5.972% | 5.160% |
| level 4 hot short | 4.776% | 4.179% |

This confirms that cross-boot sub-3% claims need A64r and that a dynamic switch
would have been preferable. The accepted production path disables the only
existing switch, however, so the experiment necessarily used three boots.

## Restart cost

B0 loaded in 153 seconds and A64r in 189 seconds; the B0 shutdown also needed
SIGTERM because a detached shell job inherited SIGINT as ignored. Total
measurement-time interruption was about 5.9 minutes plus benchmark time. Since
the candidate has no positive recurring result, no further restart or nearby
`short_read` scan is justified.

## Restored production state

The exact A64 config (`6ece1a13...`) was restored as A64r and benchmarked with
no explicit `--short-read`; the running engine therefore uses the default 64.

- server/vision/engine: `159219/159221/159226`
- engine: `/opt/ai/src/strata-freeze-throughput-20261029T0812Z/build75/strata`
- engine SHA-256: `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`
- server cwd: `/opt/ai/src/strata-two-stream-serial-20261031`
- health loaded, context 262144, 6 batch slots, 2 groups, layer split 24
- `strata.service`: inactive, MainPID 0, NRestarts 8
- swap 0; GPUs idle after the run, 280 W limits, temperatures 58/48 C

## Evidence

Remote directory:
`/opt/ai/logs/agent-hot-turn-fixed-cost-20261031T121424Z/`

| artifact | SHA-256 |
|---|---|
| A64 result | `0a829c1d14fa79cff7d9efeaa9dd1962801863075bd0cc8d7100d8d349105b18` |
| B0 result | `0f90b3816cb28cd126232660236963f8dde3ab1dc74f368cdd57495506e60d71` |
| A64r result | `57ef9b745ecc991cb81a5d25456ee9da832239677d4c2a331c530eab5a74ca63` |
| comparison | `e306dc9ec9da8dfd416053807e761fa7da3a074c819b1c1cf8a2a3d002928d15` |
| A64/A64r config | `6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811` |
| B0 config | `64721988039929395a78867e56ce2db2aa4c064a42e4cdc2f353feca8dc59794` |
| A64r server log | `47fd1d1ca509b8b239e9c7f7a5054712189f1aa64f0e5271690d767e16f9f495` |
| B0 server log | `2ae19eedb7b63b2f37ff9dc785bc81021c02df786a02aa568f62549ae8ccf3df` |

Rerun and comparison command:

```sh
python tools/server/bench-hot-turn-fixed-cost.py \
  --arm A64 --levels 1,2,4 --waves 3 \
  --context-tokens 12000 --short-fresh-tokens 24 \
  --long-fresh-tokens 596 --config <private-config> \
  --output <private-result>

python tools/server/bench-hot-turn-fixed-cost.py --compare A64.json B0.json A64r.json
```
