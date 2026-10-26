from __future__ import annotations

import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "probe_slot_admission.py"
SPEC = importlib.util.spec_from_file_location("probe_slot_admission", PATH)
assert SPEC and SPEC.loader
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def test_silence_treats_window_edges_as_events():
    assert PROBE.max_silence([], 10.0, 12.5) == 2.5
    assert PROBE.max_silence([10.4, 11.0], 10.0, 12.5) == 1.5


def test_silence_catches_gap_that_crosses_window():
    assert PROBE.max_silence([9.0, 14.0], 10.0, 13.0) == 3.0


def test_gap_can_be_clipped_to_one_stream_life():
    assert PROBE.max_gap([1.0, 2.0, 3.0, 9.0], hi=3.0) == 1.0
    assert PROBE.max_gap([1.0, 2.0, 3.0, 9.0]) == 6.0


def test_busy_slots_counts_non_idle_and_decoding_separately():
    assert PROBE.busy_slots([
        {"state": "idle"},
        {"state": "reading"},
        {"state": "decoding"},
        {"state": "decoding"},
    ]) == (3, 2)


def test_large_reader_prompt_is_unique_and_scales_with_the_requested_size():
    small = PROBE.reader_prompt("first", 512)
    large = PROBE.reader_prompt("second", 2048)

    assert "first" in small
    assert "second" in large
    assert large.count("\n") > small.count("\n")
    assert large != small
