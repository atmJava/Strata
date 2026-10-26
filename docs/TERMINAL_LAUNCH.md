# Launch and profiles

`strata serve` starts the retained OpenAI/Anthropic server. `strata launch` is an exact compatibility alias; it
does not download, install or tune anything.

## Read-only launch plan

```sh
strata serve --config /path/to/strata-022.json --plan
strata serve --config /path/to/strata-022.json --plan --output json
```

The plan validates:

- the JSON run config and its referenced tokenizer
- the local CUDA engine path or `component:strata-engine`
- the saved profile, when `--profile NAME` is used
- batch and queue options supplied on the command line
- the ready/shutdown paths used by the owner runtime

It does not load the model into GPU memory.

## Foreground serve

```sh
strata serve --config /path/to/strata-022.json
```

Useful bounded overrides:

```text
--host ADDRESS
--port PORT
--parallel N
--batch-groups N
--max-queue N
--queue-timeout SECONDS
--batch-telemetry / --no-batch-telemetry
--offline
--ready-file PATH
--shutdown-timeout SECONDS
```

These override only this process. They do not rewrite the run config.

`--engine mock` starts the API without a model or GPU. It is for transport and admission tests only.

## Saved profiles

Create and inspect a profile:

```sh
strata profile create production --from-config /path/to/strata-022.json
strata profile list --output json
strata profile show production --effective
strata profile validate production
```

Change typed serving overrides:

```sh
strata profile set production parallel=6 batch_groups=2 --plan
strata profile set production parallel=6 batch_groups=2
strata profile history production --output json
```

The command prints a revision. Save operations can require the expected revision so an old edit cannot overwrite a
newer one.

Replace the profile's source config explicitly:

```sh
strata profile use-config production \
  --from-config /path/to/new-strata.json \
  --plan
strata profile use-config production \
  --from-config /path/to/new-strata.json
```

Saving a profile does not restart a running server.

## Owned background runtime

```sh
strata runtime start --profile production --plan
strata runtime start --profile production
strata runtime status --profile production
strata runtime verify --profile production --plan --load
strata runtime verify --profile production --load
strata runtime stop --profile production --plan
strata runtime stop --profile production
```

The owner runtime stores private state below `${XDG_STATE_HOME:-~/.local/state}/strata/runtime`. It uses an
owner-only Unix socket on this Linux distribution.

Start and restart plans expose the profile revision and source SHA-256. Restart and stop plans additionally expose
the owner and instance IDs. Execute only against the reviewed identities. The owner checks them again before
draining or launching.

Restart source changes require the plan's source digest:

```sh
strata runtime restart --profile production --plan
strata runtime restart --profile production \
  --if-profile-revision REVISION \
  --if-owner-id OWNER \
  --if-instance-id INSTANCE \
  --if-source-sha256 SHA256 \
  --allow-source-switch
```

Use `--allow-source-migration` instead when the source path is unchanged but its bytes were edited in place.

## Production verification

```sh
strata check-production --config /path/to/strata-022.json
strata check-production --config /path/to/strata-022.json --output json
```

This validates the expected `sm_75` run shape and environment. It is not a replacement for compiling on the target
host, running the engine, checking both GPUs, or performing a 256K soak.

## Drafting

The source config key `drafting` accepts `auto`, `mtp`, `dflash2` or `off`.

| Value | Behavior |
|---|---|
| `auto` | MTP. DFlash2 becomes automatic only after a measured sm_75 sweep supports that change. |
| `mtp` | MTP draft layer. |
| `off` | No speculative drafting. |
| `dflash2` | Enables the retained DFlash2 path when the Qwen3.8-Flash-Next pack is ready. |

For `dflash2`, the default pack path is:

```text
<data-dir>/dflash2/qwen3.8-flash-next-dflash2.q8.pack
```

The data directory comes from the existing `--mtp` or `--pack` argument. An explicit request on another model, a
missing pack or an allocation failure falls back to MTP. The server reports the fallback rather than leaving a
half-started service.
