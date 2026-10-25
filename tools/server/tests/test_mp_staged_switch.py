"""The staged-read switch semantics are load-bearing: `=0` must disable, and the server threshold
must default to 4096 (otherwise a lost export re-opens the "first token then 38 s silence" mode).

These are static checks on the shipped patches (they are applied to the production tree, not to
this repository), so they fail loudly if someone reverts the fixes without noticing.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1] / "patches"


def test_engine_switch_zero_disables():
    patch = (ROOT / "mp-w3-0.1.47.patch").read_text()
    assert 'std::strcmp(staged_v, "0") != 0' in patch, "engine switch must treat =0 as off"
    assert 'getenv("STRATA_BATCH_STAGED_READ") != nullptr' not in patch, "bare getenv enabled =0"


def test_server_threshold_defaults_to_4096():
    patch = (ROOT / "mp-w3-server-0.2.2.patch").read_text()
    assert 'STRATA_MP_STAGED_READ_MIN_TOKENS") or "4096"' in patch, "threshold must default to 4096"
    assert 'STRATA_MP_STAGED_READ_MIN_TOKENS") or "0"' not in patch, "0 default stages every prompt"
