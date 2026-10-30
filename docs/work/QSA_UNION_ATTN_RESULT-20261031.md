# QSA-UNION-ATTN result

Run: 2026-10-31, candidate build host, 1 x RTX 2080 Ti for kernel measurements.

## Decision

**Rejected before the long-request stage.**

Three adjacent query groups were placed in one 384-thread CTA so that their unchanged selected
K/V loads would reach the same SM cache. The experiment preserved output hashes and the intended
12 resident warps per SM, but the grouped CTA was about 20% slower than the default even when all
four queries in each synthetic group selected the same cells. CTA phase alignment and final-tile
work cost more than the possible cache reuse.

The pre-registered contract stops this mechanism when the grouped microbenchmark median is not
faster than the default. Running the expensive cold 150K A-B-A would not be able to cross the
required 1% gate with a 20% kernel regression, so no candidate restart or long request was spent.

## Resource audit

The baseline int8 v2 kernel uses:

- 128 threads per CTA;
- 168 registers per thread, with no stack or local memory;
- 19,712 bytes of dynamic shared memory;
- three CTAs per SM, or 12 resident warps.

The grouped variant compiled with the same 168 registers per thread and no stack or local
memory. Its 384 threads consumed 64,512 registers, and three 19,712-byte query regions consumed
59,136 bytes of shared memory. Both fit one CTA per SM and preserve the baseline's 12 resident
warps. The experiment therefore reached the intended resource geometry.

## Exactness and timing

`qsa_prompt_attn_parity 32768 2048 1` produced identical output hashes in default and grouped
modes for every case:

| Case | Output hash |
| --- | --- |
| int8, 32K | `7ababa539cff2e15` |
| fp16, 32K | `5ae7118dd9780bf0` |
| q4_0, 32K | `6ebeb597828aee67` |
| q4_0, 1,500 context | `5918107a6e4cb65e` |
| int8, 1,500 context | `dc619269aa998d6a` |
| int8, 2,100 context | `dda4b0037582e0b5` |

For the int8 32K case:

| Selection | Default | Three-query CTA | Change |
| --- | ---: | ---: | ---: |
| Parity's ordinary selection | 9.716 ms | 11.664 ms | +20.05% slower |
| `QA_SAME=4`, all four neighbours share one set | 9.815 ms | 11.852 ms | +20.75% slower |

The second row is the best case for cache reuse: it is already at least as much sharing as any
adjacent-query mechanism could provide. The grouped path still regresses.

## Recovery

- The source was restored to sha256 `1d1181f6901b223468d309cd37e5492f21c5e508f011167956a6fbfac6affa04`.
- Only `pa_bench` and `qsa_prompt_attn_parity` were rebuilt with the experimental source. The
  running candidate engine was not replaced and did not need a restart.
- The baseline engine remains at pid 491135 with the previously recorded binary hash
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.
- Candidate health stays loaded, diagnostic environment stays absent, and production
  `strata.service` stays inactive/dead with `MainPID=0`.

## Evidence

Remote directory: `/opt/ai/logs/qsa-union-attn-20261030T171808Z/`

- `contract.md`
- `qsa_prompt_attn.baseline.cu`
- `qsa_prompt_attn.cu`
- `resource.txt`
- `parity-default.txt`
- `parity-group3.txt`
- `same4-default.txt`
- `same4-group3.txt`
- `pa_bench.baseline`
- `qsa_prompt_attn_parity.baseline`

## Not measured

This result does not show that every exact K/V union algorithm would fail. It rejects this
lowest-risk cache-co-residency design. A future design would need to reduce duplicated memory
traffic without lowering occupancy or adding the same CTA-wide phase cost, and its own
pre-registered kernel gate must pass first.
