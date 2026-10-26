# Install and build the server

This distribution has no desktop installer, updater or model downloader. The operator provides Python, CUDA, a
working `strata` engine binary and a prepared Qwen3.8-Flash-Next model.

## 1. Check the host

The supported host is:

- Linux x86_64
- 2 x NVIDIA RTX 2080 Ti 22 GB
- CUDA compute capability 7.5 (`sm_75`)
- a working NVIDIA driver reported by `nvidia-smi`
- CMake 3.24 or newer and CUDA 13.0 to build the engine

Run:

```sh
nvidia-smi
python3 --version
cmake --version
/usr/local/cuda-13.0/bin/nvcc --version
```

The Python dependencies are in [`requirements.txt`](../requirements.txt).

## 2. Build the engine

From the repository root:

```sh
cmake -S . -B build-sm75 -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc \
  -DCMAKE_CUDA_ARCHITECTURES=75
cmake --build build-sm75 --target strata -j
```

CMake refuses an operating system other than Linux and an architecture other than 75. Removed options such as
`STRATA_ENABLE_HIP`, `STRATA_ENABLE_SYCL` and `STRATA_EXPERIMENTAL_SM60` fail at configure time when enabled.

DFlash2 is compiled into the retained engine. The optional `dflash2_*` benchmark and parity targets are available
but excluded from the default build.

## 3. Place the engine

A run config may name an absolute executable directly:

```json
{"exe": "/opt/ai/bin/strata"}
```

An older config may use the local component reference:

```json
{"exe": "component:strata-engine", "backend": "cuda"}
```

That reference resolves an executable named `strata` below:

```text
$STRATA_COMPONENTS_DIR/engine/
```

When `STRATA_COMPONENTS_DIR` is unset, the default root is:

```text
${XDG_DATA_HOME:-~/.local/share}/strata/components/engine/
```

An optional `engine-component.json` manifest can pin the entrypoint and SHA-256 of every packaged file. A manifest
must declare `"platform": "linux-x86_64"` and `"backend": "cuda"`. The resolver refuses changed or incomplete
files. A manifest is local integrity metadata, not a publisher signature.

## 4. Prepare the run config

The production profile expects these effective values:

```text
backend=cuda
arch=qwen4exp
parallel=6
batch=6
batch_groups=2
max_context=262144
kv=int8
kv_resident=32768
```

The model files, tokenizer, engine path, log path and runtime environment stay in the existing run config. This
distribution does not create or download model files.

The service environment must include:

```text
STRATA_ADMIT_SHORT_FIRST=2048
```

An `env` map inside the run config reaches the engine child, not the Python server.

## 5. Check without starting

```sh
python3 strata.py doctor \
  --profile /opt/ai/src/strata-0.2.2/strata-022.json \
  --live
python3 strata.py check-production \
  --config /opt/ai/src/strata-0.2.2/strata-022.json
python3 strata.py serve \
  --config /opt/ai/src/strata-0.2.2/strata-022.json \
  --plan
```

`check-production` is a static config and environment check. It does not prove that `nvcc` built this binary, that
both GPUs are healthy, that the engine loaded, or that 256K is stable under load.

## 6. Start and verify

Foreground:

```sh
python3 strata.py serve --config /opt/ai/src/strata-0.2.2/strata-022.json
```

Background:

```sh
python3 strata.py profile create production \
  --from-config /opt/ai/src/strata-0.2.2/strata-022.json
python3 strata.py runtime start --profile production
python3 strata.py runtime verify --profile production --load
python3 strata.py runtime status --profile production
```

`runtime verify --load` performs a bounded one-token generation through the API. It is a startup gate, not a
throughput, quality or mixed-context soak.

## DFlash2 pack

The explicit DFlash2 path expects:

```text
<data-dir>/dflash2/qwen3.8-flash-next-dflash2.q8.pack
```

For an `args` list containing `--mtp /model/mtp`, `<data-dir>` is `/model`. A missing or incompatible pack falls
back to MTP. The current production host does not have this pack, so keep `drafting: "auto"` or `"mtp"` until the
pack is installed and separately measured on `sm_75`.
