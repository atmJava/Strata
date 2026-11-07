# Freeze 0.2.7 cleanup: the Python gates are green

Date: 2026-11-07. Branch `freeze/server-adapt-0.2.7`, HEAD `ad297d35`.

## Context

`331f30bf` (2026-10-27) converged this tree to the sm_75 CLI and server distribution: 4,322 files
changed, 4,091 deleted, about 994,000 lines removed. The 32 commits after it (2026-11-04 to
2026-11-07) removed what the prune left pointing at deleted code: dead imports, unused values and
helpers, tests whose headers no longer exist, printed hints that named deleted launchers, and 22
documentation pages that describe code this checkout does not carry.

The series' commit messages deferred one final run of the Python gates to the end of the series
rather than repeating it per commit. This is that run.

## Gates

| Command | Passed | Skipped | Time | Exit |
|---|---:|---:|---:|---:|
| `python -m pytest tools -q --continue-on-collection-errors` | 402 | 51 | 52.62 s | 0 |
| `python -m pytest serve tests/terminal tools/server/tests -q` | 1,457 | 64 | 514.49 s | 0 |

The second gate also reported 10,910 passed subtests. Both gates had zero failures and zero
collection errors.

Environment: Kali Linux x86_64, Python 3.13.12, pytest 9.0.3, no NVIDIA card and no `nvcc`. The 51
`tools` skips are the pack cases that need llama.cpp's `gguf-py`, which is not here. The terminal
gate's 64 skips match the count recorded before the cleanup series.

The terminal test that failed once under load before the series
(`tests/terminal/test_runtime.py::RuntimeTests::test_foreground_jsonl_emits_ready_and_stop_without_human_stdout`)
passed in this run.

## Not run

- The CUDA `sm_75` build and `ctest`: this machine has cmake but no `nvcc` and no NVIDIA card. The
  six CTest failures recorded in `L-092` are not re-checked here.
- The real-server smoke on two RTX 2080 Ti cards (six slots, tool calls, images, concurrency).
- The manual Terminal TUI (the Textual monitor shell), which the pytest gate does not exercise.

## Reproduce

```sh
python -m pytest tools -q --continue-on-collection-errors
python -m pytest serve tests/terminal tools/server/tests -q
```
