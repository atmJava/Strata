"""Tests for tools/server/tap_parity.py: the DFlash2 tap dump diff (no GPU, synthetic files)."""
import importlib.util
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location("tap_parity", ROOT / "tools" / "server" / "tap_parity.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # dataclasses looks the module up by name
    spec.loader.exec_module(mod)
    return mod


tp = _load()


def make(path, layers, rows, n_embd=2):
    """rows: {(pos, token): {layer: (a, b)}} -> a dump with `layers` in that order."""
    vals = {}
    for key, per_layer in rows.items():
        flat = []
        for layer in layers:
            flat.extend(per_layer[layer])
        vals[key] = flat
    tp.write_dump(path, n_embd, layers, vals)
    return path


def test_a_file_reads_back_what_was_written(tmp_path):
    p = make(tmp_path / "a.stap", [5, 15], {(0, 7): {5: (1.0, 2.0), 15: (3.0, 4.0)}})
    d = tp.read_dump(p)
    assert d.layers == [5, 15]
    assert d.n_embd == 2
    assert d.rows[(0, 7)] == [1.0, 2.0, 3.0, 4.0]


def test_the_header_matches_what_verify_cpp_writes(tmp_path):
    p = make(tmp_path / "a.stap", [5], {(3, 9): {5: (0.5, -0.5)}})
    raw = Path(p).read_bytes()
    magic, version, n_embd, n_taps = struct.unpack("<4i", raw[:16])
    assert (magic, version, n_embd, n_taps) == (0x50415453, 1, 2, 1)
    assert struct.unpack("<i", raw[16:20])[0] == 5
    pos, tok = struct.unpack("<2i", raw[20:28])
    assert (pos, tok) == (3, 9)


def test_two_identical_files_compare_equal(tmp_path):
    rows = {(0, 7): {5: (1.0, 2.0), 15: (3.0, 4.0)}, (1, 8): {5: (5.0, 6.0), 15: (7.0, 8.0)}}
    a = make(tmp_path / "a.stap", [5, 15], rows)
    b = make(tmp_path / "b.stap", [5, 15], rows)
    compared, differing, worst, notes = tp.compare(tp.read_dump(a), tp.read_dump(b), 1e-6)
    assert (compared, differing, worst) == (2, 0, 0.0)


def test_a_changed_float_is_reported_with_its_layer_and_position(tmp_path):
    rows_a = {(0, 7): {5: (1.0, 2.0), 15: (3.0, 4.0)}}
    rows_b = {(0, 7): {5: (1.0, 2.0), 15: (3.0, 4.5)}}
    a = make(tmp_path / "a.stap", [5, 15], rows_a)
    b = make(tmp_path / "b.stap", [5, 15], rows_b)
    compared, differing, worst, notes = tp.compare(tp.read_dump(a), tp.read_dump(b), 1e-6)
    assert (compared, differing) == (1, 1)
    assert worst == pytest.approx(0.5)
    assert any("layer 15" in n and "position 0" in n for n in notes)


def test_a_row_only_in_one_file_fails_by_default_and_can_be_allowed(tmp_path):
    a = make(tmp_path / "a.stap", [5], {(0, 7): {5: (1.0, 2.0)}, (1, 8): {5: (1.0, 2.0)}})
    b = make(tmp_path / "b.stap", [5], {(0, 7): {5: (1.0, 2.0)}})
    compared, differing, _, notes = tp.compare(tp.read_dump(a), tp.read_dump(b), 1e-6)
    assert differing == 1, "a partly written dump must not compare equal"
    assert any("in one file only" in n for n in notes)
    compared, differing, worst, _ = tp.compare(tp.read_dump(a), tp.read_dump(b), 1e-6, allow_missing=True)
    assert (compared, differing, worst) == (1, 0, 0.0)


def test_two_files_of_different_width_are_not_comparable(tmp_path):
    a = make(tmp_path / "a.stap", [5], {(0, 7): {5: (1.0, 2.0)}}, n_embd=2)
    b = make(tmp_path / "b.stap", [5], {(0, 7): {5: (1.0, 2.0, 3.0)}}, n_embd=3)
    with pytest.raises(tp.FormatError):
        tp.compare(tp.read_dump(a), tp.read_dump(b), 1e-6)


def test_a_stage_missing_a_row_is_a_clean_format_error_on_merge(tmp_path):
    a = make(tmp_path / "a.stap", [5], {(0, 7): {5: (1.0, 2.0)}, (1, 8): {5: (1.0, 2.0)}})
    b = make(tmp_path / "b.stap", [15], {(0, 7): {15: (1.0, 2.0)}})
    with pytest.raises(tp.FormatError):
        tp.merge([tp.read_dump(a), tp.read_dump(b)], [str(a), str(b)])


def test_info_prints_the_layers_and_rows(tmp_path, capsys):
    p = make(tmp_path / "a.stap", [5, 15], {(0, 7): {5: (1.0, 2.0), 15: (3.0, 4.0)}})
    assert tp.main(["info", str(p)]) == 0
    out = capsys.readouterr().out
    assert "2 taps" in out and "[5, 15]" in out and "1 rows" in out


def test_disjoint_stage_files_merge_into_the_whole_window(tmp_path):
    stage0 = make(tmp_path / "s0.stap", [5], {(0, 7): {5: (1.0, 2.0)}})
    stage1 = make(tmp_path / "s1.stap", [15, 25], {(0, 7): {15: (3.0, 4.0), 25: (5.0, 6.0)}})
    out = tmp_path / "whole.stap"
    assert tp.main(["merge", str(stage0), str(stage1), "-o", str(out)]) == 0
    d = tp.read_dump(out)
    assert d.layers == [5, 15, 25]
    assert d.rows[(0, 7)] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]


def test_merge_refuses_overlapping_layers(tmp_path):
    a = make(tmp_path / "a.stap", [5], {(0, 7): {5: (1.0, 2.0)}})
    b = make(tmp_path / "b.stap", [5], {(0, 7): {5: (1.0, 2.0)}})
    with pytest.raises(tp.FormatError):
        tp.merge([tp.read_dump(a), tp.read_dump(b)], [str(a), str(b)])


def test_compare_of_a_merge_with_a_single_stage_file_is_the_parity_check(tmp_path):
    whole = make(tmp_path / "w.stap", [5, 15], {(0, 7): {5: (1.0, 2.0), 15: (3.0, 4.0)}})
    s0 = make(tmp_path / "s0.stap", [5], {(0, 7): {5: (1.0, 2.0)}})
    s1 = make(tmp_path / "s1.stap", [15], {(0, 7): {15: (3.0, 4.0)}})
    merged = tp.merge([tp.read_dump(s0), tp.read_dump(s1)], [str(s0), str(s1)])
    compared, differing, _, _ = tp.compare(tp.read_dump(whole), merged, 1e-6)
    assert (compared, differing) == (1, 0)


def test_a_file_with_an_extra_layer_is_not_the_same_measurement(tmp_path):
    a = make(tmp_path / "a.stap", [5], {(0, 7): {5: (1.0, 2.0)}})
    b = make(tmp_path / "b.stap", [5, 15], {(0, 7): {5: (1.0, 2.0), 15: (3.0, 4.0)}})
    compared, differing, _, notes = tp.compare(tp.read_dump(a), tp.read_dump(b), 1e-6)
    assert differing == 1, "a dump missing a tap layer must not pass"
    assert any("only in the second file" in n for n in notes)
    assert tp.main(["compare", str(a), str(b)]) == 1
    assert tp.main(["compare", str(a), str(b), "--allow-missing"]) == 0, "the common layers do agree"


def test_a_duplicate_record_is_a_format_error(tmp_path):
    p = make(tmp_path / "a.stap", [5], {(0, 7): {5: (1.0, 2.0)}})
    p2 = tmp_path / "dup.stap"
    p2.write_bytes(Path(p).read_bytes() + Path(p).read_bytes()[20:])   # one record twice
    with pytest.raises(tp.FormatError):
        tp.read_dump(p2)


def test_trailing_bytes_are_a_format_error(tmp_path):
    p = make(tmp_path / "a.stap", [5], {(0, 7): {5: (1.0, 2.0)}})
    p.write_bytes(Path(p).read_bytes() + b"\x00\x00\x00\x00")
    with pytest.raises(tp.FormatError):
        tp.read_dump(p)


def test_a_bad_magic_is_a_format_error(tmp_path):
    p = tmp_path / "bad.stap"
    p.write_bytes(b"\x00" * 32)
    with pytest.raises(tp.FormatError):
        tp.read_dump(p)


def test_a_truncated_record_is_a_format_error(tmp_path):
    p = make(tmp_path / "a.stap", [5], {(0, 7): {5: (1.0, 2.0)}})
    raw = Path(p).read_bytes()
    p.write_bytes(raw[:-4])
    with pytest.raises(tp.FormatError):
        tp.read_dump(p)


def test_the_exit_code_says_whether_they_agree(tmp_path, capsys):
    rows = {(0, 7): {5: (1.0, 2.0)}}
    a = make(tmp_path / "a.stap", [5], rows)
    b = make(tmp_path / "b.stap", [5], rows)
    assert tp.main(["compare", str(a), str(b)]) == 0
    c = make(tmp_path / "c.stap", [5], {(0, 7): {5: (1.0, 9.0)}})
    assert tp.main(["compare", str(a), str(c)]) == 1
    assert tp.main(["compare", str(a), str(tmp_path / "missing")]) == 2
