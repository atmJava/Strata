"""S0c: an engine that ends before the staged read starts must not leave `mp_staged` behind.

The staged-read patch (tools/server/patches/mp-w3-server-0.2.2.patch) sets `self.mp_staged = True`
before sending `BREAD ...`.  If the engine's stdin is already gone, `_send` raises `EngineDied`
*before* `phase = "staged"`, so `generate_batched`'s `finally` does not see the staged phase and the
flag stays True; every later admission then waits for a slot that will never come (until a restart
rebuilds the engine object).  The test runs the patched server against a dead pipe and asserts the
flag is cleared on the way out.

The server file is patched into a temp copy of the 0.2.2 fixture, so the patch stays the artifact
under test (see staged_patch_harness).
"""
import threading

import pytest

from staged_patch_harness import new_engine, patched_module


def test_engine_dying_on_bread_clears_mp_staged(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATA_MP_STAGED_READ", "1")
    monkeypatch.setenv("STRATA_MP_STAGED_READ_MIN_TOKENS", "0")
    module = patched_module(tmp_path)
    engine = new_engine(module)

    cancel = threading.Event()
    stream = engine.generate_batched([1] * 5000, 16, {}, cancel)
    with pytest.raises(module.EngineDied):
        for _token in stream:
            pass

    assert engine.mp_staged is None, "the staged flag leaked: later admissions would wait for a slot forever"
