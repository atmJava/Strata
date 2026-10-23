# Server hardware: adaptation plan

This is the working plan for `atmJava/Strata`, a fork of
[Niko1221/Strata](https://github.com/Niko1221/Strata) taken at `011c9e60` (0.2.2, engine 0.1.47). The fork exists
for one purpose: to make Strata run well on server hardware, and to keep that work in one branch so it can be
compared with upstream at any time.

**Status: skeleton.** The scope below is a proposal, not a decision. Everything said about the code comes from
reading it, not from a build or a run, and is unconfirmed static analysis. Nothing here is measured yet.

## What "server hardware" is meant to cover

Not settled. These are the candidates, to be cut down before any code changes:

- **Cards that are not sold for a desktop.** SXM modules, cards with NVLink between them, passively cooled cards
  made for a chassis with its own airflow, and the datacenter parts the desktop release notes never mention.
- **A machine with no display.** No monitor, no X session, reached over SSH or a remote console, and a GPU that is
  never opened by a window system.
- **Many cores, many sockets, ECC memory.** Where the CPU side of loading and the RAM tier of the model behave
  differently from a desktop.
- **More than two cards, or two cards of different sizes.** Upstream says it has no NVIDIA rig of that size
  ([docs/TEST_REQUESTS.md](TEST_REQUESTS.md)).
- **A container or a service manager instead of a terminal.** Docker, systemd, Kubernetes.

## What the code already does (read, not measured)

| Area | Where | Why it matters on a server |
|---|---|---|
| Device enumeration, name, free and total memory, capability | `src/core/device.cu` | One place decides what a card is; a datacenter card or an SXM module has to be named and gated correctly here |
| Build architectures | `CMakeLists.txt` (`CMAKE_CUDA_ARCHITECTURES`, default `120`; `sm_90+` gates for clusters and PDL; the `STRATA_HIP_GFX906` HIP path) | A server card is usually an older or a larger compute capability than a current desktop card, so a default build may not target it |
| Which card takes which layer, and whether the model fits | `strata_control/gpu_fit.py`, `gpu_roles.py`, `vram_fit.py` | Unequal cards, more than two cards, and a card with no display attached |
| What hardware is found at all | `strata_control/discovery.py` | The first thing that breaks on an unknown platform |
| Machine tuning and autotune | `strata_control/tune_machine.py`, `autotune.py`, `hipblaslt_tuning.py`, `tune_knobs.py` | Tuning that assumes a desktop power and thermal envelope |
| Containers | `docker/compose/docker-compose.nvidia.yml`, `docker/compose/docker-compose.amd.yml` | The likely way this runs on a server |

Multi-GPU behaviour, the split planner and the PCIe paths are described in [MULTI_GPU.md](MULTI_GPU.md), which is
the first thing to re-read before touching them. The engine's own account of memory tiers is in
[DETAILS.md](DETAILS.md).

## Questions to answer before the first code change

1. Which machine, and what exactly is in it? Cards (model, memory, link, count), CPU, RAM, storage, operating
   system and driver version. Without this the rest is guesswork.
2. Does the stock release run on it at all? If it does, the work is tuning and defaults; if it does not, the work
   is first a bug report to upstream.
3. Is the target "it runs" or "it runs faster"? Upstream only changes a default where a measurement shows it
   faster ([CONTRIBUTING.md](../CONTRIBUTING.md)), and the same rule applies here.
4. Keep the change local to this fork, or offer it upstream? A change that helps only this machine is a fork
   change; a change that helps any machine with the same card is a pull request.

## Steps

1. **Inventory the box.** Record the cards, the CPU, the RAM, the driver and the operating system in this file,
   and check them against what the engine reports at start-up.
2. **Baseline.** Build from this branch unchanged and run the benchmark protocol in
   [docs/BENCHMARK_PROTOCOL.md](BENCHMARK_PROTOCOL.md) and [docs/COMMUNITY_BENCHMARKS.md](COMMUNITY_BENCHMARKS.md):
   three runs per size, the exact command, what was not tested.
3. **Build matrix.** Add the compute capabilities the server cards need to the build, and check that the default
   build is unchanged for the cards upstream supports.
4. **Placement.** Check the split planner and the RAM tier on this machine's real sizes; only then change
   anything.
5. **Headless and service.** Run it with no display and under a service manager; fix what breaks.
6. **Record.** Every number goes in this file with the machine it came from.

## Rules

- Upstream's rules apply to this branch: [CONTRIBUTING.md](../CONTRIBUTING.md) and [AGENTS.md](../AGENTS.md). The
  default path stays byte-identical to the release; anything new is opt-in.
- No claim without a measurement, and say what it was measured on. A finding from reading code is marked as
  unconfirmed static analysis, as above.
- One branch, one purpose. If the work splits into unrelated parts, it gets another branch.

## This machine

TODO: fill in after step 1.

| | |
|---|---|
| Fork point | `011c9e60` (0.2.2) |
| Cards | TODO |
| CPU | TODO |
| RAM | TODO |
| Operating system | TODO |
| Driver | TODO |
