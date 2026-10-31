# AGENT-PLE-INFLIGHT result

Date: 2026-10-31

Status: adopted as an explicit configuration argument. The direct PLE reader's
default queue depth remains 256, so existing configurations are byte-identical.

Contract:
[`AGENT_PLE_INFLIGHT_CONTRACT-20261031.md`](AGENT_PLE_INFLIGHT_CONTRACT-20261031.md)

## Result

The real 49-day Codex resume with a 223K-token history showed that cold PLE
reads were the largest removable prefill phase. Increasing the existing direct
reader queue from 256 to 1,024 improved the cold prefill consistently in three
adjacent unprofiled pairs:

| Pair | direct-256 prompt | 1,024 prompt | Prompt improvement | direct-256 wall | 1,024 wall | Wall improvement |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 123,571.7 ms | 120,024.4 ms | 2.87% | 130.198 s | 130.127 s | 0.05% |
| 2 | 130,616.7 ms | 129,175.0 ms | 1.10% | 137.576 s | 136.071 s | 1.09% |
| 3 | 123,543.4 ms | 116,645.9 ms | 5.58% | 130.352 s | 123.564 s | 5.21% |

The median of the three paired improvements is 2.87% for server prompt time
and 1.09% for client wall. All six measurements used the same stored session,
zero cached prompt tokens, no tool execution and a four-token sentinel answer.
Every request returned `finish=stop`, and the three candidate requests completed
without HTTP or engine errors.

Client wall had a roughly 3.5-second startup/completion outlier in pair 1.
Server prompt time remained positive in every pair, and the paired wall result
is positive in every pair. The adoption is therefore scoped to this cold PLE
path; it is not a claim of a universal 2.87% task improvement.

The earlier profile arms independently showed the same direction:

| Arm | Host PLE wait | Dominant PLE phase | Server prompt |
|---|---:|---:|---:|
| profile, direct 256 A1 | 50,362 ms | 39,291 ms | 135,073.4 ms |
| profile, direct 256 A2 | not captured | not captured | 132,645.8 ms |
| profile, 1,024 | 43,453 ms | 18,525 ms | 128,303.5 ms |

A1/A2 prompt drift was 1.80%, below the pre-registered 3% limit. The 1,024
profile arm reduced server prompt by 5.01% against A1 and 3.27% against A2.
The `mmap` arm was already rejected at 28.4% higher wall.

## Configuration and rollback

The only adopted delta from the production B2 configuration is:

```text
--ple-inflight 1024
```

The engine binary is unchanged. The source default remains
`max_inflight = 256`; the change is an explicit saved configuration argument.
The direct-read slab grows from about 2 MiB to 8 MiB, which is negligible
beside the model and host memory.

Current process:

```text
server  89451
vision  89453
engine  89458
```

Health is loaded, engine version is `0.1.51`, slots/groups are 6/2, swap is
zero and available RAM after the request was 13,219 MiB. `strata.service`
remains inactive. The exact rollback config is `final-direct-config.json`.

## Evidence

Evidence directory:
`/opt/ai/logs/agent-ple-inflight-20261031T092456Z/`

| Artifact | SHA-256 |
|---|---|
| baseline config | `4b396f441ef30f62ab5398cf7b18b3ed902175fd70db7a789ff9a9c14f7e0a5e` |
| candidate config | `6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811` |
| baseline pair 1 metrics | `7e1aa389a244caed774a667564b9dd436ba57361754e4ddbe38c4c5d6bc0c60d` |
| baseline pair 2 metrics | `7728e551cdfce9abdd56c3f1b79cccdaf8a1e0a8bd22388af6440d43ae651a76` |
| baseline pair 3 metrics | `7f1b114c0a0075b4335dd2da78f2873787bf8240a5dc42e40018def8c2aa8ac5` |
| candidate pair 1 metrics | `0ad6a4ad33aacc63bf249b42b2485b20c94b071f6842ab264e0fd0ae872d78a1` |
| candidate pair 2 metrics | `196e032740c412c4d5c600ab9cc24b4f1b34acfbc98842c2cf9b39edf0d1485c` |
| candidate pair 3 metrics | `8ecf82171b5d27d5741789a1389145a4f0b23a85d4a767de8597fb678c8e765e` |

Private client outputs remain under `/tmp/agent-ple-inflight-final-*`; prompt,
assistant, reasoning and tool bodies were not committed and were not printed.

## Limits

This result covers one real aged session and three cold boot pairs. It does not
establish the population frequency of aged-session resumes, and it does not
retest 2/4/6/8 concurrent traffic because queue depth changes only PLE read
parallelism. A larger prompt with more page deduplication could have a
different optimum.

The remaining PLE cost is still large enough to justify a separate row-cache
locality experiment. Do not combine that experiment with inflight, mmap,
keep-alive or kernel changes.
