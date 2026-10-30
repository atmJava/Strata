# UTIL-PIPED-MTP-ORDER result

Run: 2026-10-31 on the candidate snapshot, two RTX 2080 Ti, CUDA 13.0, engine
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.

## Decision

**UTIL-PIPED-MTP-ORDER is rejected by the frozen parity gate.** GPU CPU-order expert
arithmetic did not make the piped-MTP candidate repeatable or equal to A.

The candidate copied the rejected piped-MTP B config and appended only:

```text
--expert-cache-cpu-order
```

All other arguments and environment entries remained identical to B. The candidate kept six slot
drafters, two pipelined groups, `expert_slots=17104`, and the requested engine hash. It became
health-loaded, but the quality gate failed before any candidate throughput run.

## Frozen A3 baseline

The restored A baseline ran `tools/server/bench-strata.py` at 2, 4 and 6 streams, three
repetitions each and 400 requested output tokens:

| Streams | Aggregate median tok/s | Wall median s | TTFT median ms | Foreign requests |
| ---: | ---: | ---: | ---: | ---: |
| 2 | 81.0 | 9.9 | 142.0 | 0 |
| 4 | 116.1 | 13.6 | 293.0 | 0 |
| 6 | 133.2 | 17.8 | 967.5 | 0 |

Draft acceptance medians were `79.77%`, `81.3%` and `82.39%`. All measured windows contained
exactly the expected requests. The fixed prompts also produced valid early `finish_reason=stop`
completions: one four-stream request at 367 tokens; six-stream requests at 362, 376 and 395
tokens; and one single request in a six-stream round at 392 tokens. Every response completed
without a transport error. The stop completions remain in their measured token totals and are not
presented as failures. This baseline is retained, but because parity failed it is not used to make
a throughput claim for the candidate.

## Parity failure

Probe 0 (`STRATA_PARITY_ALPHA`) and probe 1 (arithmetic result `944`) were repeatable and equal to
A. Probe 2 failed twice:

```text
D trial 0:
Because the target model accepts only tokens that match its own greedy argmax choice, so verification
rejects any mismatch and preserves the original greedy sequence.

D trial 1:
Speculative decoding preserves greedy output because the target model verifies each proposed token
and accepts only those matching its own greedy argmax choice.
```

Those are byte-for-byte the same two outputs produced by the previous B run. A produced one
different, repeatable 96-token answer and finished with `length`. Thus CPU-order did not affect the
observed output at all.

Static inspection found that `--expert-cache-cpu-order` is wired to `ExpertDispatch::hit_cpu_order`.
The layer-split verifier uses `VerifyHits`/grouped kernels and has no corresponding CPU-order field
or switch. This is static analysis, not a proof that no other path was involved, but it explains why
the switch had no effect on this shape.

## Restart cost and restoration

- A to candidate: stop 8 seconds, launch to loaded 146 seconds.
- Candidate parity finished at `19:45:42Z`; candidate-to-A stop took 9 seconds.
- A restore took 171 seconds from launch to health-loaded.
- End-to-end interruption from A stop start to restored A loaded was 6 minutes 51 seconds.

Restored A:

- server/engine/vision PID `525579/525603/525588`;
- health `loaded=true`;
- `batch_slots=6`, `batch_groups=2`, `expert_slots=17274`, `expert_cache_mib=28267`;
- engine sha256 `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- config sha256 `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`;
- no `STRATA_PIPED_MTP` and no CPU-order argument;
- production `strata.service` unchanged at `inactive/dead`, `MainPID=0`, `NRestarts=23`.

Final resources: `14.2 GiB` RAM available, swap used `12 MiB`, both GPUs idle after loading.

## Evidence

- `/opt/ai/logs/util-piped-mtp-order-a3-20261030T193825Z.log`
  (sha256 `267589d3cffb87e46fff854646527cb9a3cba4f0bd6c952e3b217dd613814acf`)
- `/opt/ai/logs/util-piped-mtp-order-a3-final-metrics-20261030T193825Z.json`
  (sha256 `224b3b9fbd7fae0b8ac975c5a9e1cd250b2a13779a48c6d8a2fa4cdbb96d34e4`)
- `/opt/ai/logs/qsa-diag-20261031T1653Z/d-piped-mtp-cpu-order.json`
  (sha256 `f65491d5fb52627201c7011f5d2c1f6b95d0439a9b656b1ae6d57de5a6651f3f`)
- `/opt/ai/logs/util-piped-mtp-order-parity-D-20261030T1945Z.json`
  (sha256 `6c1c3304bf884d008c1ac74f03541424523116a1c036f9e20694aad71f9f90eb`)
- `/opt/ai/logs/util-piped-mtp-order-candidate-20261030T194151Z.out`
  (sha256 `97331947e585bf4c2ef7ed0427aeb5325e702fc8fae887b96e06aa6ceeb97ce4`)
- `/opt/ai/logs/util-piped-mtp-order-D-final-metrics-20261030T194542Z.json`
  (sha256 `02732e42ee90a724fbfe460d68a1edd86ad6d1bd2fd6be7549bbddbc13f30932`)
- `/opt/ai/logs/util-piped-mtp-order-restore-A-20261030T194542Z.out`
  (sha256 `184f2454b2d411cbca7a5f39a0c8c0f6386b23454fd11c071c5f4a098b428743`)
- `/opt/ai/logs/util-piped-mtp-order-switch-A-to-D-20261030T194151Z.txt`
  (sha256 `2b86b8a9d8655df263a10ab5cbe13791765d05621310c578f71d593014be8f68`)

## Next question

The piped-MTP quality branch has now rejected its exact cache method and its existing arithmetic
switch. The next independent opportunity is the 2-stream scheduling trough. Test only the existing
one-group versus two-group schedule on the unchanged A model; if one group wins at two streams but
harms 4/6/8 streams, the result defines a concurrency-aware gating implementation without changing
model arithmetic.

