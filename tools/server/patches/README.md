# W3 staged-read patches (engine 0.1.47 / server 0.2.2)

Two patches implement "read without a slot, adopt when one frees" (W3). They are **not** applied to
the repository tree: the repository is engine 0.1.48 / server 0.2.3, while production is
engine 0.1.47 / server 0.2.2. Apply them to a copy of the production tree.

| File | Target | Adds |
|---|---|---|
| `mp-w3-0.1.47.patch` | `src/program/generate.cpp` (engine 0.1.47) | `BREAD`/`BRDY` staged read, `BADOPT` adoption, `BCANCEL` cancel |
| `mp-w3-server-0.2.2.patch` | `serve/server.py` (server 0.2.2) | the no-slot read path, adoption, threshold, debug prints |

## Apply, build, deploy

```sh
# 1. a copy of the production tree (never patch the running tree in place)
cp -a /opt/ai/src/strata-0.2.2 /opt/ai/src/strata-mp
cd /opt/ai/src/strata-mp

# 2. apply (apply --check first; both patches are context-clean against 0.1.47 / 0.2.2)
patch -p1 < /path/to/mp-w3-0.1.47.patch
patch -p1 < /path/to/mp-w3-server-0.2.2.patch
python3 -m py_compile serve/server.py

# 3. build (Release, sm_75; ~10.6 min) and verify the artifact
cmake --build build --target strata -j6
md5sum build/strata

# 4. deploy with a snapshot + rollback (see docs/MULTIPATH_W3.md "生产启用")
```

## Switches (exact semantics)

- Engine `STRATA_BATCH_STAGED_READ`: **unset/empty/`0` = off**; any other value = on.
  (Before 2026-10-25 the code used a bare `getenv() != nullptr`, so `=0` still enabled it — fixed.)
- Server `STRATA_MP_STAGED_READ`: `1` = on, anything else = off.
- Server `STRATA_MP_STAGED_READ_MIN_TOKENS`: default **4096**; `0` = stage every prompt
  (short prompts then show the "first token, then silence until a slot frees" pattern — not recommended).
- Engine side also needs `STRATA_BATCH_STAGED_READ` in `strata-022.json`'s `env`; the server flag must be
  **exported in `run-022.sh`** (the config `env` reaches only the engine child).

## Provenance

- Built from the production tree copy `/opt/ai/src/strata-mp`; the deployed binary's md5 is recorded in
  `docs/HANDOVER.md` §13 and `docs/STATE.md`.
- `git apply --check` in this repository passes for both patches (they target the files' 0.1.47/0.2.2 text).
