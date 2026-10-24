# `tools/server/tests/` — black-box acceptance (T1–T4)

These are the **T-tests** of [`docs/FEATURE_PARITY.md`](../../../docs/FEATURE_PARITY.md) §4: plain HTTP against
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
MP_BASE_URL=http://192.168.3.14:8080 MP_API_KEY=111 python3 -m pytest tools/server/tests -v
```

Environment: `MP_BASE_URL` (default `http://127.0.0.1:8080`), `MP_API_KEY` (default `111`, never hardcoded in a
test), `MP_MODEL` (default `qwen3.8-flash-next-iq3_xxs`).

## Expected status (2026-10-24, before W3)

| test | expected | why |
|---|---|---|
| `test_mp_api.py` (T1) | **pass** | the server's API contract is in production |
| `test_mp_batch.py` (T2) | pass or **skip/xfail** | exactness settings (`STRATA_IQ_MT_MIN=1` etc.) are not production; mismatch is reported |
| `test_mp_admission.py` (T3) | **xfail** | waits until /metrics reports 4 busy slots, then first AND second token ≤2 s; the xfail message carries the numbers (L-048) |
| `test_mp_prefix.py` (T4) | **pass** | prompt cache is on in production; `cached_tokens` is reported |
| `test_mp_spec.py` (T5) | **pass** | offer≥20 and acceptance≥50% from `/metrics` totals; the window may include other traffic |

When W3 lands, T3's `pytest.xfail` turns into an assertion (delete the xfail branch); an xfail that stays after the
feature ships is a failed acceptance, not a pass.

## Hygiene

These tests send real requests. Do not run them while a measurement is in flight, and do not run them with a
subagent generating (that is the same server's second stream, HANDOVER §9.16). One run at a time; record the log
path in the round's `MULTIPATH_LOG` entry.

## Files

| file | test |
|---|---|
| `mp_client.py` | shared HTTP helpers (stdlib only) |
| `conftest.py` | `server` fixture: skips with a clear reason when the server is unreachable |
| `test_mp_api.py` | T1: health, models, non-stream/stream usage, 400, 401 |
| `test_mp_batch.py` | T2: greedy solo vs batch |
| `test_mp_admission.py` | T3: 4 streams + cold reader; 4-busy-slot gate, first + second token ≤2 s, neighbour stall ≤1 s |
| `test_mp_prefix.py` | T4: second read hits the prefix cache |
| `test_mp_spec.py` | T5: draft acceptance floor (spec must be on) |
