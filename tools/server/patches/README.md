# W3 staged-read patches (engine 0.1.47 / server 0.2.2)

Two patches implement "read without a slot, adopt when one frees" (W3). They are the **production
artifact**: production is engine 0.1.47 / server 0.2.2, and they are applied to a copy of that tree.

**Status (2026-10-25 20:45, checked on this checkout).** The branch `hw/server-adapt-0.2.3` carries
its own committed staged read (`681fd256`: `src/program/generate.cpp` +125 lines, `serve/server.py`
+169; finished by `46d12fa9`). The patches are therefore no longer a diff against the repository — they
are only how W3 gets onto the older production tree:

- `git apply --check` **fails** for both patches against this tree (engine patch stops at
  `src/program/generate.cpp:11224`, server patch at `serve/server.py:1138`). Expected: the feature is
  already in the repository's 0.2.3 text.
- The server patch is now **at parity with `46d12fa9`**: `BCANCEL_DRAIN_S` (10 s drain when a staged
  read is cancelled) and the strict `BRDY` parse are in the patch (regenerated 2026-10-25 20:45 from
  the `v0.2.2` tag text; the previous copy lacked both).
- The base the patch is tested against is a fixture, `base-0.2.2/serve/server.py` = `git show
  v0.2.2:serve/server.py` (md5 `aec97a89…`), pinned in `staged_patch_harness.py`. Before this, the
  harness patched the repository file and both protocol tests failed with "Reversed (or previously
  applied) patch detected" (measured 2026-10-25 20:14: 2 failed / 55 passed / 10 skipped).

| File | Target | Adds |
|---|---|---|
| `mp-w3-0.1.47.patch` | `src/program/generate.cpp` (engine 0.1.47) | `BREAD`/`BRDY` staged read, `BADOPT` adoption, `BCANCEL` cancel |
| `mp-w3-server-0.2.2.patch` | `serve/server.py` (server 0.2.2) | the no-slot read path, adoption, threshold, debug prints, R12/R13 fixes, `BCANCEL_DRAIN_S`, strict `BRDY` |
| `base-0.2.2/serve/server.py` | — | the unpatched 0.2.2 text (test fixture, not deployed) |

**Production is not the patch tree (measured 2026-10-25 20:40).** The running server is
`/opt/ai/src/strata-0.2.2/serve/server.py`, md5 `a3c7df58…`, while the patched tree is
`/opt/ai/src/strata-mp/serve/server.py`, md5 `52b84d54…`. The deployed copy is the patch **plus** the
R12/R13 fixes, a staged-wait `yield "ping"` keep-alive and the S2 `done["metrics"]` timing, and it
still **lacks** `46d12fa9`'s two fixes; it matches no commit on this branch. `docs/STATE.md` (through
R11) recorded `52b84d54` as the server hash, which was the staging tree's hash, not production's —
V1-1 exists to stop that. See `docs/STATE.md` "版本戳基线".

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

## Fixes

Both fixes below are in the patch as committed 2026-10-25 (V0); they were in the working copy only
before that, so a deploy from a clean checkout lacked them.

- 2026-10-25 (R12): a staged read whose engine died on the `BREAD` send left `mp_staged=True` (`phase` was
  set after `_send`), so every later admission waited for a slot until the engine was restarted. `phase` is
  now set before the send; `tools/server/tests/test_mp_staged_patch.py` pins it (red before, green after).
- 2026-10-25 (R13): cancelling a staged read that waits for a slot sent `BCANCEL` and drained `BCANCELLED`
  without the control lines (the `cancel.is_set()` return happens while they are given back), so the drain could
  read another request's `T`/`DONE`. The `finally` now takes the control lines first (and holds `mp_staged` until
  then); `tools/server/tests/test_mp_staged_cancel.py` pins it. Cost: it can wait for a solo stream to give the
  lines back.

## Switches (exact semantics)

- Engine `STRATA_BATCH_STAGED_READ`: **unset/empty/`0` = off**; any other value = on.
  (Before 2026-10-25 the code used a bare `getenv() != nullptr`, so `=0` still enabled it — fixed.)
- Server `STRATA_MP_STAGED_READ`: `1` = on, anything else = off.
- Server `STRATA_MP_STAGED_READ_MIN_TOKENS`: default **4096**; `0` = stage every prompt
  (short prompts then show the "first token, then silence until a slot frees" pattern — not recommended).
- Engine side also needs `STRATA_BATCH_STAGED_READ` in `strata-022.json`'s `env`; the server flag must be
  **exported in `run-022.sh`** (the config `env` reaches only the engine child).

## Patch-based tests (green: the patch is what they run)

`test_mp_staged_patch.py`, `test_mp_staged_cancel.py` and `test_mp_patch_parity.py` copy the
`base-0.2.2/serve/server.py` fixture to a temp directory and apply `mp-w3-server-0.2.2.patch` to the
copy (`staged_patch_harness.py`), so the patch stays the artifact under test. They pin the two shipped
protocol fixes (an engine dying on `BREAD` clears `mp_staged`; a staged cancel holds the control lines
and drains for 10 s; a malformed `BRDY` is an error). Measured 2026-10-25 20:45 on this checkout:
`pytest tools/server/tests -q` = **61 passed / 10 skipped**, and the three files alone = 4 passed in
0.5 s. (Before the fixture, the same suite was 2 failed / 55 passed / 10 skipped — the harness patched
the repository file, where the patch had nothing left to add.)

The same protocol is covered on this tree by `serve/test_staged_wedge.py`, which needs no patch:
**3 passed in 2.22 s** (2026-10-25 20:14, `pytest serve/test_staged_wedge.py -q`).

## Provenance

- Built from the production tree copy `/opt/ai/src/strata-mp`; the deployed binary's md5 is recorded in
  `docs/HANDOVER.md` §13 and `docs/STATE.md`.
- History: both patches applied cleanly to the repository as it stood before `681fd256` (checked
  2026-10-25 20:16 against `681fd256^`: `patch -p1 --dry-run` takes every hunk, with offsets of 208–330
  lines). `681fd256` committed the staged read into the repository, so the same check now fails for
  both — see the Status block above. The patches still target the 0.1.47 / 0.2.2 text they were built
  against.
- Regeneration (server patch): patch a copy of `base-0.2.2/serve/server.py`, edit the result, then
  `diff -u --label a/serve/server.py --label b/serve/server.py <base> <patched> > mp-w3-server-0.2.2.patch`;
  check it applies to the fixture and that the four protocol tests pass. Do not regenerate it from this
  tree: the repository text already contains the feature and the diff would be empty.
