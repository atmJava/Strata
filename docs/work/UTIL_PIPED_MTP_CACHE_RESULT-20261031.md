# UTIL-PIPED-MTP-CACHE result

Run: 2026-10-31 on the candidate snapshot, two RTX 2080 Ti, CUDA 13.0, engine
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.

## Decision

**UTIL-PIPED-MTP-CACHE is rejected at the startup gate.** The explicit per-device cache count cannot
be used in this two-GPU layer-split shape.

The candidate copied the rejected piped-MTP config and changed only:

```text
--expert-cache auto       -> --expert-cache 10065
append --expert-cache-device1 7209
```

The engine read `--expert-cache-device1` as a helper cache on a GPU that runs no layer-split stage.
Both visible GPUs already run one of the two stages, so startup stopped before loading weights:

```text
strata generate: --expert-cache-remote with a layer split needs a GPU that runs no stage
(2 visible, 2 used by the split)
```

The server ended with exit code 2 and never became loaded. No parity probe, benchmark request or
throughput comparison was run. This result rejects the pre-registered configuration method, not piped
MTP itself and not a valid explicit cache limit implemented for layer-split stages.

## Candidate proof

Source B config:
`/opt/ai/logs/qsa-diag-20261031T1653Z/b-piped-mtp.json`
(sha256 `e22fb10cdfcc277eac739008b100feb3eab1ee9f35d73f89e9e96eea64637bc7`).

Candidate config:
`/opt/ai/logs/qsa-diag-20261031T1653Z/c-piped-mtp-cache-neutral.json`
(sha256 `877514d023ebb1fed4f5689242e980adc2c40f10ab43a4086bb4a6c8c3452525`).

The JSON diff contained only the two intended cache changes. `--batch-mtp`,
`STRATA_PIPED_MTP=1`, `STRATA_BATCH_SELF_COMMIT=0`, `parallel=6`, `--batch-groups 2` and every
other argument and environment entry remained equal to B.

## Restart cost and restoration

The A-to-candidate switch was necessary because the candidate needed different process arguments.

- A stop began at `2026-10-30T19:27:47Z` and completed at `19:27:55Z` (8 seconds).
- The candidate failed at `19:28:19Z`, 24 seconds after launch and before model loading.
- A restore began immediately at `19:28:19Z` and reached health-loaded at `19:31:34Z`.
- Total service interruption from stop start to A loaded was 3 minutes 47 seconds.

The restored A baseline is:

- server/engine/vision PID `522908/522922/522917`;
- health `loaded=true`;
- `batch_slots=6`, `batch_groups=2`, `expert_slots=17274`, `expert_cache_mib=28267`;
- engine sha256 `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- config sha256 `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`;
- no active `STRATA_PIPED_MTP`; `STRATA_BATCH_SELF_COMMIT=1`;
- production `strata.service` unchanged at `inactive/dead`, `MainPID=0`, `NRestarts=23`.

Final resource state was safe: `14.1 GiB` RAM available, swap used `12 MiB`, and both GPUs idle
after loading.

## Evidence

- `/opt/ai/logs/util-piped-mtp-cache-server-20261030T192747Z.out`
  (sha256 `4979ba0d9de2d7be4b5f1ba6658cb71f040ad533e2124a3adbb3bc60cc949645`)
- `/opt/ai/logs/util-piped-mtp-cache-restore-A-20261030T192819Z.out`
  (sha256 `b0e1957fd80ff01b042db0ed72be45ae82c855916032d947ca5e577281907eef`)
- `/opt/ai/logs/util-piped-mtp-cache-switch-A-to-C-20261030T192747Z.txt`
  (sha256 `3f17f519cc5c6cdf5bad04b8d97e761538fd6fd7b79c2f3665d75554bdd299bf`)

## Next question

The existing `--expert-cache-cpu-order` switch makes GPU expert arithmetic follow the CPU's
accumulation order. It can test whether the observed parity drift is explained by GPU/CPU expert
rounding without trying to force an unsupported per-stage cache count. The next contract adds only
that switch to the unchanged piped-MTP B config and gates all performance work on byte-identical
repeatable output.

