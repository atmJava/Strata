"""The shipped 0.2.2 patch carries the two fixes that landed in the repository with 46d12fa9.

`mp-w3-server-0.2.2.patch` is the artifact deployed to production, so "the repository has it" is not
enough: these two assertions run against the *patched* 0.2.2 text (see staged_patch_harness):

1. a malformed `BRDY` reply is a protocol error, not a read of 0 tokens;
2. cancelling a staged read drains `BCANCELLED` for `BCANCEL_DRAIN_S` (10 s), not the 300 s default.

Both were missing from the patch until V0 (2026-10-25); production ran the older behaviour.
"""
import queue
import threading

import pytest

from staged_patch_harness import new_engine, patched_module


def test_malformed_brdy_is_a_protocol_error(tmp_path):
    module = patched_module(tmp_path)
    engine = new_engine(module)
    engine._ctl_mode = "batch"
    engine.lines = queue.Queue()
    engine.lines.put("BRDY 5000 16")          # the continuation flag is truncated

    with pytest.raises(ValueError, match="malformed staged-read reply"):
        for _line in engine._control(threading.Event(), lambda _token: None):
            pass


def test_cancel_drain_uses_the_bounded_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATA_MP_STAGED_READ", "1")
    monkeypatch.setenv("STRATA_MP_STAGED_READ_MIN_TOKENS", "0")
    module = patched_module(tmp_path)
    engine = new_engine(module)
    engine.slot_busy = [True] * 4             # every slot busy: the staged read must wait for one

    assert module.BCANCEL_DRAIN_S == 10.0
    timeouts = []

    def send(_self, text):
        if text.startswith("BREAD "):
            _self._ctl_result = ("brdy", 5000, 16, True)

    def control(_self, _cancel, _on_token, **_kw):
        return iter(())

    def drain(_self, _until, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return "BCANCELLED"

    engine._send = send.__get__(engine, module.StrataEngine)
    engine._control = control.__get__(engine, module.StrataEngine)
    engine._drain_control = drain.__get__(engine, module.StrataEngine)

    cancel = threading.Event()

    def leave():
        cancel.set()
        with engine.slot_cv:
            engine.slot_cv.notify_all()

    timer = threading.Timer(0.02, leave)
    timer.start()
    try:
        for _token in engine.generate_batched([1] * 5000, 16, {}, cancel):
            pass
    finally:
        timer.join()

    assert timeouts == [10.0], f"the staged cancel drained with {timeouts}, not the 10 s bound"
