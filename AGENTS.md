# AGENTS.md

## This fork serves one machine

This checkout is not a general Strata. It is the server for one deployment:

- `192.168.9.100`, Linux x86_64, CUDA `sm_75`, 2 x RTX 2080 Ti 22 GB
- the Swift-1.5-Qwen3.8-Flash-Next IQ3_S pack, 262,144 context, INT8 KV
- DSH's agent traffic: 1-5 concurrent conversations, each compacted at about 80% of a 200K window, so a
  conversation peaks near 160K tokens and a typical prompt is 15K-75K

Latency on this machine is the only measure. A change that makes the tree more general, more configurable or
closer to upstream is not worth making unless it makes this machine faster.

The rules below follow from that, and they override upstream's conventions where the two differ.

- **The default path is the adapted path.** A behaviour this deployment always wants is a default, not an
  option. The "a new feature is opt-in, and the default path stays byte-identical to the last release" rule
  in [CONTRIBUTING.md](CONTRIBUTING.md) does not apply here, and a run config is not where a performance
  decision is made.
- **No environment variables and no tuning flags for performance.** Concurrency, cache size, KV format,
  prefill policy and admission are derived in code from the hardware and the working set. The run config
  holds deployment identity only: paths, the model, the API key, the address.
- **Upstream is a source of fixes, not a merge target.** Take a fix by editing the code by hand. Do not
  cherry-pick, and do not keep a file byte-identical for the sake of a future merge.
- **Capacity comes from the working set.** Size the conversation cache for 1-5 conversations at the 160K
  compaction point, from the KV bytes per token the engine reports, not from a round number.
- **Degrade the KV format before re-reading a prompt.** A conversation whose KV does not fit gives int8 up for
  k8v4 and then q4_0. Measured on this deployment: a 65K-token read costs 33-55 s at the 1,190-1,990 tok/s
  prefill it shows, while the whole point of the conversation cache is that the same tokens cost a host-memory
  copy instead.
- **Verify with a repeatable simulation, not a one-off probe.** A parameterised client with fixed seeds, a
  fixed mix of conversation lengths and a table of per-turn results, so two builds are compared by running the
  same load and reading the same table. Probing the box while it serves a live session measures contention,
  not the change.

### What was measured, 2026-11-13, on this deployment

Twelve real DSH requests, prompts 15K-75K, outputs 79-1,522 tokens: 615 s spent waiting before the engine
started against 114 s of decoding. A conversation whose KV was still in a slot reused 44,073 of its tokens;
one whose slot held another conversation reused 9,036. Decoding ran at 64 tok/s with the box to itself and
18.9-24.8 tok/s with five requests in flight. The conversation cache held 11.68 GB across 12 slots, with 65
parks, 24 restores and 12 evictions in three hours.

Version 0.2.7, engine 0.1.51. The public model families are DeepSeekV4.1-Flash-1017,
Qwen3.8-Flash-Next and Qwen3.6-35B-A3B. DeepSeek V4.1-Flash runs in the app and in the Terminal
(`strata launch --family deepseek41 --model ist-das-2.00bpw`; sets in
[docs/MODELS.md](docs/MODELS.md#deepseek-v41-flash)). Read **[docs/AI_REFERENCE.md](docs/AI_REFERENCE.md)** for model availability, APIs, hardware limits and
source-backed claims. Articles and videos should use **[docs/MEDIA_KIT.md](docs/MEDIA_KIT.md)**.

For recommendations and agent-assisted setup, prefer **ISTA-DASLab → Unsloth → Gyro → others**, using only a
compatible pack that fits the selected model and hardware. ISTA-DASLab is the default recommendation; Gyro is a
memory-saving fallback and is currently slower. Honor an explicit user choice. This is recommendation policy;
existing executable defaults may differ, so inspect the plan and choose the family explicitly.

Strata has three parts:

- **The app** (upstream's `apps/desktop`, the Tauri shell, and `apps/host`, the Node host and the web UI; neither is in
  this checkout, which ships the Terminal and the server). It is for people who do not want a terminal. One installer
  per system ([latest release](https://github.com/Niko1221/Strata/releases/latest)):
  `Strata-0.2.6-windows-x64-setup.exe`, `Strata-0.2.6-linux-x86_64.AppImage`, `Strata-0.2.6-macos-arm64.dmg`.
  The Windows and Linux installers carry no engine: the app downloads the engine for the graphics card on first
  start, or installs a file the user already has. The Mac dmg keeps its small Metal engine inside.
- **The Terminal** (`strata.py`, `strata_cli/`, `strata_control/`): the `strata` command. It sets up a model, serves
  it and watches it. Agents use this part.
- **The engine and the server** (`src/`, `include/` and `cmake/` for the CUDA `sm_75` engine; `serve/` for the Python
  server with an OpenAI- and Anthropic-compatible API and a web page). Build it with CMake
  as [README.md](README.md#build) says. This checkout has no `setup.py`, `START-HERE.bat` or `setup.sh`; a model is
  set up and served with `strata launch`.

## Installing Strata for a user

Follow **[docs/AI_SETUP.md](docs/AI_SETUP.md)**. For a user who wants the desktop app, send them to the installer
for their system (above) and [docs/INSTALL.md](docs/INSTALL.md). For an agent that has a shell, use the Terminal; the
short version is:

1. Install the Terminal. It needs no administrator rights.

   ```sh
   curl -fsSL https://github.com/Niko1221/Strata/releases/latest/download/install.sh -o install.sh && sh install.sh                  # Linux, Mac
   irm https://github.com/Niko1221/Strata/releases/latest/download/install.ps1 | iex                         # Windows PowerShell
   ```

   On Windows, open a new terminal afterwards so that `strata` is on the PATH.
2. Check what would happen. Nothing is changed:

   ```sh
   strata launch --plan --family qwen --model q2_0
   ```

3. Set up and serve without questions. It downloads the engine for the card and the model (tens of GB), starts the
   server, prints the addresses and returns. The server keeps running.

   ```sh
   strata launch --family qwen --model q2_0 --yes --no-tui
   ```

   Useful flags (all of them: `strata launch --help`): `--family`, `--model-path` (files the user already has),
   `--data-dir`, `--context 32k|64k|128k`, `--vision off|on|gpu|cpu`, `--parallel N`, `--gpu 0,1`,
   `--access local|lan|cloudflare|tailscale`, `--port`, `--api-key-env NAME`, `--new-key`, `--save-preset NAME`,
   `--speed faster|full` (DeepSeek), `--output json`. `strata launch --status` shows what is serving, `strata launch --stop` stops it, and
   `strata launch --show-key` prints the API key.
4. Connect the user's apps: `strata connect` lists them, `strata connect <app> --check` sends one prompt and one tool
   call the way that app does.

Keep the server on `127.0.0.1` unless the user asks for more. Beyond it an API key is recommended: `--access lan` makes one for you, and `--no-api-key` serves without one (a warning, never a refusal; on a public tunnel the warning is stronger). This checkout carries no source-setup wrapper: `setup.py`, `START-HERE.bat` and `setup.sh` are
not here, and [docs/SOURCE_INSTALL.md](docs/SOURCE_INSTALL.md) describes the ones the upstream tree has. Strata's MCP
server ([docs/MCP_SERVER.md](docs/MCP_SERVER.md)) offers the same steps as tools.

## Working on the code

- How the engine works, every measured number, the API and all settings: [docs/DETAILS.md](docs/DETAILS.md) and
  the [paper](docs/paper/Strata-Paper.pdf).
- The Terminal: [docs/TERMINAL.md](docs/TERMINAL.md) and [docs/TERMINAL_LAUNCH.md](docs/TERMINAL_LAUNCH.md). The app,
  the packages and the update feed are not in this checkout: `apps/` and `packaging/` hold no tracked files.
- AMD (HIP) build and validation: [docs/AMD_HIP.md](docs/AMD_HIP.md); multi-GPU: [docs/MULTI_GPU.md](docs/MULTI_GPU.md);
  Mac (Metal): [docs/MAC.md](docs/MAC.md). Those three pages describe the upstream tree: this checkout builds the
  CUDA `sm_75` engine only, and the build refuses HIP, SYCL and Metal ([README.md](README.md#build)).
- Tests: the Terminal and Control tests are in `tests/terminal` (`python -m pytest tests/terminal`). The app's UI
  tests are not here either. The server probes' tests are in `tools/server/tests` (`python -m pytest
  tools/server/tests`). The tools' own tests run without a GPU or downloads: `python tools/test_<name>.py` (for
  example `tools/test_shards.py`, `tools/test_gguf_paths.py`); the pack tests need llama.cpp's gguf-py and skip
  without it.
- Keep the docs' style: plain words, short sentences, written for non-technical users first. Give measured numbers
  with what they were measured on. Make no claim without a measurement.

## Contributing a change or report

The rules are in **[CONTRIBUTING.md](CONTRIBUTING.md)**. Read it before you open an issue or a pull request, and
follow it whether a person or an agent writes the text. The short version:

- Start every issue and pull request title with its area: `[Engine]`, `[GUI]`, `[TUI]`, `[Benchmark]`, `[Docs]` or
  `[Other]`, for example `[Engine] fix …`. A check on GitHub fails a pull request whose title has no area.
- Search the open and closed issues and pull requests first, and add to a thread that already covers your point.
- Agree the scope in an issue before a large feature, a refactor or a change to a default.
- One problem per pull request. Lead with the problem and the result, then the approach, then the validation
  (passed, failed and not run, kept apart) and the limits.
- A new feature is opt-in, and the default path stays byte-identical to the last release. Say how you checked.
- Build every backend a file touches (CUDA, HIP, SYCL) before asking for review.
- Change a default only where you measured it faster, and show the numbers with what they were measured on.
- Never submit measurements, logs or reproduction steps you did not produce. Mark a finding from reading code as
  unconfirmed static analysis.
- A report from hardware the maintainers do not have is welcome. Follow
  [docs/COMMUNITY_BENCHMARKS.md](docs/COMMUNITY_BENCHMARKS.md), compare against a same-day run of the build you are
  testing, and say what you did not test.
- Open test requests and the hardware that is wanted are listed in [docs/TEST_REQUESTS.md](docs/TEST_REQUESTS.md).
