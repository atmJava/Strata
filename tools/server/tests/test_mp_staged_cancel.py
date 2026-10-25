"""S0d: cancelling a staged read that waits for a slot must not steal another request's control lines.

While a staged read waits for a free slot the request gives the control lines back around each wait
and takes them again after it.  A client that leaves is seen at `if cancel.is_set(): return`, i.e. at
a moment when it does *not* hold them; the `finally` then sent `BCANCEL` and drained `BCANCELLED`
from the shared line queue anyway, so a request holding them lost its own `T`/`DONE` lines to that
drain.  The test drives a staged read to `staged_wait`, cancels it, and asserts `BCANCEL` and the
drain happen with the control lines held.

The server file is patched into a temp copy (see staged_patch_harness).
"""
import threading

from staged_patch_harness import new_engine, patched_module


def test_cancel_while_waiting_takes_the_control_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATA_MP_STAGED_READ", "1")
    monkeypatch.setenv("STRATA_MP_STAGED_READ_MIN_TOKENS", "0")
    module = patched_module(tmp_path)
    engine = new_engine(module)
    engine.slot_busy = [True] * 4        # every slot busy: the staged read must wait for one

    sent, drained = [], []

    def send(_self, text):
        sent.append((text, _self.ctl.held))
        if text.startswith("BREAD "):
            _self._ctl_result = ("brdy", 5000, 16, True)   # the read finished; now it waits for a slot

    def control(_self, _cancel, _on_token, **_kw):
        return iter(())

    def drain(_self, _until, **_kw):
        drained.append(_self.ctl.held)
        return "BCANCELLED"

    engine._send = send.__get__(engine, module.StrataEngine)
    engine._control = control.__get__(engine, module.StrataEngine)
    engine._drain_control = drain.__get__(engine, module.StrataEngine)

    cancel = threading.Event()

    def leave():
        # the client goes away while the read waits for a slot (the lines are given back between waits)
        cancel.set()
        with engine.slot_cv:
            engine.slot_cv.notify_all()

    timer = threading.Timer(0.02, leave)
    timer.start()
    try:
        stream = engine.generate_batched([1] * 5000, 16, {}, cancel)
        for _token in stream:          # returns cleanly: a cancelled request is not an error
            pass
    finally:
        timer.join()

    bcancel = [held for text, held in sent if text == "BCANCEL"]
    assert bcancel, "the staged read was not cancelled"
    assert all(bcancel), "BCANCEL was sent while another request could hold the control lines"
    assert drained and all(drained), "BCANCELLED was drained without the control lines"
