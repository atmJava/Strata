# AGENT-PLE-ROW-CACHE result

Date: 2026-10-31

Status: rejected by the pre-registered profile-selection gate. The adopted
1,024-inflight configuration is restored.

Contract:
[`AGENT_PLE_ROW_CACHE_CONTRACT-20261031.md`](AGENT_PLE_ROW_CACHE_CONTRACT-20261031.md)

## Decision

The 4,194,304-row cache was slower on the same real aged session in adjacent
cold boots. The candidate increased server prompt time by about `16.23%` and
host PLE wait by about `60.49%`. It therefore failed the prompt and host-PLE
conditions before any unprofiled paired runs were justified.

No neighboring cache size was tried. The contract closes capacity sizing on a
profile miss.

## Profile

The A arm was the adopted 1,024-inflight configuration with only
`STRATA_PREFILL_TIMING=1` enabled. The B arm added only:

```text
--ple-row-cache 4194304
```

| field | A: adopted base | B: larger row cache | B vs A |
|---|---:|---:|---:|
| prompt tokens | 228,478 | 228,507 | +0.013% |
| cached tokens | 0 | 0 | unchanged |
| server prompt | 117,477.3 ms | 136,549.2 ms | +16.23% slower |
| client wall | 124.307 s | 143.403 s | +15.36% slower |
| GPU timeline | 94,179 ms | 99,792 ms | +5.96% slower |
| PLE phase | 22,658 ms | 28,113 ms | +24.07% slower |
| host PLE wait | 32,341 ms | 51,905 ms | +60.49% slower |

Both requests returned the correct sentinel, `finish=stop`, four output tokens,
zero tools and zero HTTP or engine errors. Six slots, two groups, health and
swap remained safe.

The result is scoped to this exact aged request and candidate. It does not show
that every larger cache must be slower. The contract deliberately allowed one
bounded test and one miss closes this axis, so no causal model or nearby-value
sweep is added after the fact.

## Restored production

The accepted manual process is:

```text
source   /opt/ai/src/strata-two-stream-serial-20261031
config   /opt/ai/logs/agent-ple-inflight-20261031T092456Z/final-candidate-config.json
server   93552
vision   93554
engine   93559
```

The engine hash is
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
the config hash is
`6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811`.
Health is loaded, context is 262144, the live shape is six slots / two groups,
swap use is zero, available RAM was 14,552 MiB and a deterministic smoke request
finished with `stop`.

The running engine contains `--ple-inflight 1024` and does not contain
`--ple-row-cache`. It also does not contain either decode-profiling variable.
`strata.service` is stopped and has `MainPID=0`.

The first restoration attempt used `/opt/ai/bin/use-config.sh`, which targets the
systemd service rather than the manual experiment process. The unit rejected the
experiment config, retried eight times and was stopped. Its prior config was
restored byte-for-byte from the timestamped backup, then the manual process was
started from the accepted config. Future manual arms must use the manual
launcher path; `use-config.sh` is not evidence that a manual process changed.

## Evidence

Evidence directory:
`/opt/ai/logs/agent-ple-row-cache-20261031T101536Z/`

| Artifact | SHA-256 |
|---|---|
| A config | `00cdad904d46a9043f5d784891c52e7778cdd1e8e76f01b9d69330644e33efd9` |
| B config | `9fdbaed52d0fe208b4e2310c87b5eb878afbfcf3ca617ab21d42cd8ab50f0b5b` |
| A metrics | `6242bdc5aee306dc5808dd92b43d0a88631969ebc73653f4c5edcf12b725f392` |
| B metrics | `1ddab3fdca010d6e1da33cd13bfcf95018a26363654421c79204138dca27fbfd` |
| A profile lines | `c0b9758109f90f64648cfe5defbc5919c723e6676344724416a5a292a1f28dd2` |
| B profile lines | `79a5bc17e40eba901413846a46b9e1c37cf4463bee7b45173fb15ec8dcfa9775` |

Private client outputs remain under `/tmp`; prompt, assistant, reasoning and
tool bodies were not committed or printed.

## Limits

This was one profile pair, accepted as sufficient because the candidate missed
the gate by a large margin in the wrong direction. It does not establish the
frequency of aged-session resumes or the performance of other hosts, models or
row-cache geometries.

The remaining cold-prefill question is structural. The next review should
compare state residency and restore mechanisms, not another local PLE capacity
or queue parameter.
