"""F1F2/T3a: the admission wait is a parameter - `queue_timeout` (config alias `admission_wait_s`).

With six slots busy a late request can only wait; how long it waits before the server says "come back later"
is a policy, not a bug.  `--queue-timeout N` (config `queue_timeout`, alias `admission_wait_s`) bounds it: the
request that still has no engine turn after N seconds gets 429 `queue_wait_timeout`.  This test pins:

- with the bound set, the wait ends on time (a long request holds the single turn) and the refusal is 429 with
  `queue_wait_timeout` (not a hang, not a 500);
- without the bound, the second request is still waiting after the same window (the default did not change).

Mock engine offline: no GPU, no production.
"""
import contextlib
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BODY = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 3, "stream": False}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def _serve(*extra: str):
    port = _free_port()
    args = [sys.executable, "-m", "serve.server", "--engine", "mock", "--host", "127.0.0.1",
            "--port", str(port), "--offline", "--script-delay", "0.5", "--script", "x" * 200, *extra]
    proc = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError("the mock server exited:\n" + proc.stdout.read())
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:  # noqa: BLE001 - not up yet
                time.sleep(0.1)
        else:
            raise AssertionError("the mock server did not become healthy in 40 s")
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _long_request(port: int):
    """Hold the single turn for ~50 s (100 tokens at 0.5 s)."""
    body = json.dumps({"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100,
                       "stream": False}).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            response.read()
    except Exception:  # noqa: BLE001 - cancelled by the test's teardown
        pass


def _second(port: int, timeout: float) -> tuple[int, dict, float]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                     data=json.dumps(BODY).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read()), time.monotonic() - started
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read()), time.monotonic() - started


def test_bounded_wait_refuses_on_time():
    with _serve("--queue-timeout", "1") as port:
        threading.Thread(target=_long_request, args=(port,), daemon=True).start()
        time.sleep(0.6)                              # the long request holds the turn now
        status, body, wall = _second(port, timeout=20)
        print(f"  T3a bounded: status {status} after {wall:.1f} s: {json.dumps(body)[:120]}")
        assert status == 429, (status, body)
        code = (body.get("error") or {}).get("code")
        assert code == "queue_wait_timeout", body
        assert wall < 5.0, f"the bound did not fire on time ({wall:.1f} s)"


def test_unbounded_wait_is_still_the_default():
    with _serve() as port:
        threading.Thread(target=_long_request, args=(port,), daemon=True).start()
        time.sleep(0.6)
        started = time.monotonic()
        try:
            _second(port, timeout=3.0)               # it should still be waiting when our own 3 s run out
            raise AssertionError("the second request finished; the single turn cannot have been free")
        except (TimeoutError, socket.timeout, urllib.error.URLError):
            pass
        assert time.monotonic() - started >= 2.5
        print("  T3a default: still waiting after 3 s (unchanged)")
