# `tools/server/tests/` — black-box acceptance (T1–T4)

These are the black-box server acceptance tests: plain HTTP against
Strata's OpenAI-compatible server, standard library only. They need no GPU, no engine internals, and no vLLM code —
that is the point: the fork's own tests (`tests/entrypoints/openai`, `tests/v1/core/...`) run inside vLLM and cannot
run here; what they define (the behaviour) is what these tests check.

## Run

On the server:

```sh
cd /opt/ai/bin/tests          # after scp of this directory
MP_BASE_URL=http://127.0.0.1:8080 MP_API_KEY=111 python3 -m pytest -v \
  | tee /opt/ai/logs/MP-tests-$(date -u +%Y%m%dT%H%M%SZ).log
```

From this repository, over the LAN:

```sh
MP_BASE_URL=http://192.168.9.103:8080 MP_API_KEY=111 python3 -m pytest tools/server/tests -v
```

Environment: `MP_BASE_URL` (default `http://127.0.0.1:8080`), `MP_API_KEY` (default `111`, never hardcoded in a
test), `MP_MODEL` (default `qwen3.8-flash-next-iq3_xxs`).

## Expected status (2026-10-27)

Local, no server: `python3 -m pytest tools/server/tests -q` = **69 passed / 10 skipped / 0 failed**.
The 10 skips are the live-server files below (T1/T2/T3/T4/T5): from this workstation there is no server
on `127.0.0.1:8080`, so the fixture skips them with the reason — **a skip is not a pass**.  With
`MP_BASE_URL` pointing at the box they run for real, one run at a time.

| test | expected | why |
|---|---|---|
| `test_mp_api.py` (T1) | pass with a live server | the server's API contract |
| `test_mp_batch.py` (T2) | pass or **skip/xfail** | exactness settings (`STRATA_IQ_MT_MIN=1` etc.) are not production |
| `test_mp_admission.py` (T3) | **xfail** | waits for busy slots; production stages only prompts ≥4096 tokens, so a 452-token reader stays un-staged (L-053) |
| `test_mp_prefix.py` (T4) | pass | prompt cache is on in production |
| `test_mp_spec.py` (T5) | pass | offer≥20 and acceptance≥50% from `/metrics` totals |
| `test_mp_preflight.py`, `test_mp_round_gate.py` | pass | state/queue logic and the legacy Gate checker |
| `test_mp_staged_switch.py` | pass | `STRATA_*STAGED_READ=0` really turns it off (source-level) |
| `test_mp_timings.py`, `test_mp_admission_limit.py`, `test_mp_admission_wait.py`, `test_mp_disconnect_keepalive.py` | pass | mock-engine protocol cases; no server needed |
| `test_tap_parity.py` | pass | tap dump compare/merge |

## Dependencies on a bare machine

`pytest` plus the two packages `serve/server.py` imports at load time: **`jinja2`** (through
`serve/frontend.py`) and **`regex`** (through `tools/strata_tokenizer.py`).  The mock-server cases spawn
`serve/server.py`, so without them they fail with `ModuleNotFoundError` instead of testing anything —
measured 2026-10-25 on the V1 tree: 12 failed with only `pytest`; 8 failed after `jinja2`; 0 failed after
`regex` too (`/opt/ai/tmp/venv-v0`, `/tmp/v1-pip.log`).

## Hygiene

These tests send real requests. Do not run them while a measurement is in flight, and do not run them with a
subagent generating (that is the same server's second stream, HANDOVER §9.16). One run at a time; record the log
path in the `ITERATION_LOG.md` retrospective.

## Files

| file | test |
|---|---|
| `mp_client.py` | shared HTTP helpers (stdlib only) |
| `conftest.py` | `server` fixture: skips with a clear reason when the server is unreachable |
| `test_mp_api.py` | T1: health, models, non-stream/stream usage, 400, 401 |
| `test_mp_batch.py` | T2: greedy solo vs batch |
| `test_mp_admission.py` | T3: 4 streams + cold reader; 4-busy-slot gate, first + second token ≤2 s, neighbour stall ≤1 s |
| `test_mp_admission_limit.py` | admission limit: 429 `admission_full` and its error body (S1) |
| `test_mp_admission_wait.py` | a request that waits for a slot, and the keep-alive it sees (S8/F1F2) |
| `test_mp_disconnect_keepalive.py` | a client that leaves frees the slot (S7) |
| `test_mp_prefix.py` | T4: second read hits the prefix cache |
| `test_mp_spec.py` | T5: draft acceptance floor (spec must be on) |
| `test_mp_preset.py` | T7: `check_preset.py` refuses wrong presets (unit, no server) |
| `test_mp_timings.py` | per-request `done["metrics"]`: keys, monotonic queue ≤ ttft ≤ wall (S2) |
| `test_mp_staged_switch.py` | the staged-read switches' exact semantics (S0) |
| `test_mp_preflight.py` | `preflight.py` checks the queue/state pair, not the server |
| `test_mp_round_gate.py` | legacy `round_gate.py`: old 11-field logs, wording and queue counts |
| `test_probe_version_stamp.py` | T-probe hashes and checks the engine/server paths actually running |
| `test_tap_parity.py` | `tap_parity.py compare/merge` on synthetic dumps |
| `probe_long_read.py` | not a test: the staged-read neighbour-stall probe (R11/L-055) |
| `probe_slot_admission.py` | not a test: fill every slot, then report the late reader's queue/TTFT/wall and neighbour silence |
| `probe_cancel_staged.py`, `probe_cancel_wait.py` | not tests: S0c/S0d probes against a live server |
