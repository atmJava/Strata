# AI setup guide

Use this guide when an agent has a shell on the Linux server. The task is to run this CLI/server distribution for
an existing Qwen3.8-Flash-Next installation. Do not install a desktop app, fetch another model family, switch to
AMD/Intel/Mac, or replace the production config.

## 1. Establish the target

Confirm:

```sh
uname -s
nvidia-smi --query-gpu=name,compute_cap,memory.total --format=csv,noheader
python3 --version
/usr/local/cuda-13.0/bin/nvcc --version
```

Expected:

- Linux
- 2 x RTX 2080 Ti
- compute capability `7.5`
- CUDA toolkit available if an engine build is needed

Do not claim the cards are healthy or the engine is sm_75 merely because `nvidia-smi` prints their names.

## 2. Inspect before changing

```sh
python3 strata.py --version
python3 strata.py doctor --profile /opt/ai/src/strata-0.2.2/strata-022.json --live
python3 strata.py check-production --config /opt/ai/src/strata-0.2.2/strata-022.json
python3 strata.py serve --config /opt/ai/src/strata-0.2.2/strata-022.json --plan
```

Read the actual run config before editing. Preserve its model, tokenizer, engine path, log path and environment.
This tree has no model downloader.

Recall that `STRATA_ADMIT_SHORT_FIRST=2048` must be set in the systemd service environment. An `env` map in the run
config reaches the engine child, not the Python server.

## 3. Build only when needed

```sh
cmake -S . -B build-sm75 -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc \
  -DCMAKE_CUDA_ARCHITECTURES=75
cmake --build build-sm75 --target strata -j
```

The build rejects non-Linux hosts, other CUDA architectures and removed backend options. A successful Python check
does not prove that this compile succeeded.

## 4. Save the profile

```sh
python3 strata.py profile create production \
  --from-config /opt/ai/src/strata-0.2.2/strata-022.json
python3 strata.py profile validate production
python3 strata.py runtime start --profile production --plan
```

Do not execute `runtime start` until the operator accepts the plan.

## 5. Start and verify

```sh
python3 strata.py runtime start --profile production
python3 strata.py runtime status --profile production
python3 strata.py runtime verify --profile production --plan --load
python3 strata.py runtime verify --profile production --load
```

If verification uses reviewed identities, use a fresh plan for every attempt.

## 6. Check the API

Use an API key from the environment; never print it:

```sh
curl -fsS http://127.0.0.1:8080/health
curl -fsS -H "Authorization: Bearer $STRATA_API_KEY" \
  http://127.0.0.1:8080/v1/models
```

The server supports OpenAI `/v1/chat/completions`, Anthropic `/v1/messages`, `/v1/responses`, `/v1/models`,
`/props`, `/health`, `/slots` and `/metrics`.

## DFlash2

DFlash2 is retained. Use it only when the exact Qwen3.8-Flash-Next DFlash2 pack exists:

```text
<data-dir>/dflash2/qwen3.8-flash-next-dflash2.q8.pack
```

Set `"drafting": "dflash2"` in the source config. A missing or incompatible pack falls back to MTP. The current
production host lacks the pack, so do not report DFlash2 as enabled or measured there.

## Stop

```sh
python3 strata.py runtime stop --profile production --plan
python3 strata.py runtime stop --profile production
```

## Report accurately

Separate these states:

- static Python tests passed
- CUDA compilation passed or was not run
- engine process started
- model loaded
- API health passed
- one-token generation passed
- two-card telemetry observed
- 256K mixed soak passed

Never imply a later state from an earlier one. See [AI_REFERENCE.md](AI_REFERENCE.md) for the current evidence and
limits.
