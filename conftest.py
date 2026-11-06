r"""pytest: the Terminal and the server keep their per-user files under the config, state and data homes - the saved
profiles and connections (~/.config/strata, %APPDATA%\Strata on Windows), the run records (~/.local/state/strata:
runtime/, tuning/, time-skip/) and the engine folder (STRATA_COMPONENTS_DIR, else the user's data folder). A test
that reaches them must never touch the real files, so the whole session gets its own homes."""
import os
import tempfile

_home = tempfile.mkdtemp(prefix="strata-pytest-config-")
os.environ["APPDATA"] = _home
os.environ["XDG_CONFIG_HOME"] = _home
# The STATE and DATA homes too, and APPDATA / LOCALAPPDATA as their Windows names: the code here reads the XDG ones
# (strata_control/paths.py, strata_control/profiles.py, strata_cli/connections.py, strata_control/engine_component.py).
# A test that installs a stub engine writes into the state home - on 2026-10-24 one such run left an entry in the
# owner's real tuning\recheck.json there. A test that wants a place of its own still names it (STRATA_COMPONENTS_DIR, state_base=...).
os.environ["LOCALAPPDATA"] = _home
os.environ["XDG_STATE_HOME"] = _home
os.environ["XDG_DATA_HOME"] = _home
