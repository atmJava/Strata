# Model and production profile

This distribution targets one model family: Qwen3.8-Flash-Next on 2 x RTX 2080 Ti with CUDA `sm_75`.

## Production shape

The current measured production configuration uses:

| Setting | Value |
|---|---|
| backend | `cuda` |
| engine architecture | `qwen4exp` |
| serving slots | 6 |
| batch groups | 2 |
| context | 262144 tokens |
| KV cache | INT8 |
| resident KV | 32768 tokens |
| expert cache | 28222 MiB |
| expert slots | 17248 |
| free VRAM after start | 1005 MiB / 1073 MiB |

The free-VRAM values are startup readings from the production host after the 256K switch. They are not a promise
of headroom under every workload.

Validate a config with:

```sh
strata check-production --config /path/to/strata-022.json
strata check-production --config /path/to/strata-022.json --output json
```

The check also requires the 12 switches in
[`../tools/server/production-env-2080ti.json`](../tools/server/production-env-2080ti.json).

## Context and memory

The engine loads or maps a prepared Qwen3.8-Flash-Next model. This repository does not download model files or
choose a provider. The run config must already point at the correct model pack, tokenizer, engine and model
metadata.

At 256K, the production host reports:

- `batch_slots=6`
- `kv_resident=32768`
- `expert_slots=17248`
- `expert cache=28222 MiB`
- two-card startup free VRAM of `1005/1073 MiB`

A 253,633-token needle request passed at 90% depth. That is about 96.8% of the 262,144-token limit; it does not
cover a full-window request, multiple simultaneous long reads, cancellation or a one-hour mixed soak.

## DFlash2

DFlash2 remains supported for Qwen3.8-Flash-Next. The retained engine contains the DFlash2 kernels and the server
accepts the explicit `dflash2` drafter.

Pack path:

```text
<data-dir>/dflash2/qwen3.8-flash-next-dflash2.q8.pack
```

Source config:

```json
{
  "model_name": "qwen3.8-flash-next-iq3_xxs",
  "drafting": "dflash2",
  "args": [
    "--mtp", "/path/to/model/mtp",
    "--max-context", "262144"
  ]
}
```

With `--mtp /path/to/model/mtp`, `<data-dir>` is `/path/to/model`.

The server adds:

```text
--draft dflash2 --draft-model <data-dir>/dflash2/qwen3.8-flash-next-dflash2.q8.pack
```

If the model is not Qwen3.8-Flash-Next, the pack is absent, or the engine cannot allocate it, the server falls back
to MTP and reports the reason. `drafting: "auto"` currently selects MTP. Keep the production default on MTP until a
same-boot `sm_75` sweep shows DFlash2 winning across prompt classes.

The current production host does not have the DFlash2 pack. The retained path is therefore present in source and
has CPU-side tests, but no current target-host generation or acceptance-rate result exists for it.

## Evidence

Current measured production state and limits are recorded in:

- [STATE.md](STATE.md)
- [HANDOVER.md](HANDOVER.md)
- [EVIDENCE_LEDGER.md](EVIDENCE_LEDGER.md)

Use those files for exact dates, hashes, benchmark methods and untested cases. Do not turn a startup value into a
throughput claim or a single successful request into a soak result.
