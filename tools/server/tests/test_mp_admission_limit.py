"""S1: the admission limit -- over the limit a request is refused with 429, not queued forever.

`--max-queue N` bounds the inference calls waiting beyond the engine's slots
(`serve/server.py:3963`, refusal at `serve/server.py:6772`), and `max-queue 0` is meant to refuse
overload outright.  The item is "already implemented, missing test": this test starts the server
with the mock engine (no GPU, no production), keeps one request in flight, and asserts the second
one is refused with 429 + `admission_full` + `Retry-After`, that a refusal is not sticky (a later
request succeeds), and that `--max-queue 2` lets the same second request wait instead of refusing
it.  Without the second arm a blanket 429 would pass the first one.
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
BODY = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100, "stream": False}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(port: int, timeout: float = 120.0) -> tuple[int, dict, dict]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                 data=json.dumps(BODY).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


@contextlib.contextmanager
def _serve(*extra: str):
    port = _free_port()
    args = [sys.executable, "-m", "serve.server", "--engine", "mock", "--host", "127.0.0.1",
            "--port", str(port), "--offline", "--script-delay", "0.02", "--script", "x" * 200, *extra]
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


def _in_flight(port: int, out: list):
    out.append(_post(port))


def test_over_the_limit_is_refused_and_not_sticky():
    with _serve("--max-queue", "0") as port:
        first = []
        t = threading.Thread(target=_in_flight, args=(port, first), daemon=True)
        t.start()
        time.sleep(0.4)                      # the 200-token mock read is in flight (2 s at 20 ms/token)
        status, body, headers = _post(port)
        assert status == 429, f"a second call over the limit got {status}: {body}"
        assert body["error"]["code"] == "admission_full", body
        assert headers.get("Retry-After") == "1", headers
        t.join(120)
        assert first and first[0][0] == 200, first

        status, body, _ = _post(port)        # the refusal did not leave the counter stuck
        assert status == 200, f"a call after the limit cleared got {status}: {body}"


def test_max_queue_two_lets_the_second_wait():
    with _serve("--max-queue", "2") as port:
        first = []
        t = threading.Thread(target=_in_flight, args=(port, first), daemon=True)
        t.start()
        time.sleep(0.4)
        status, body, _ = _post(port)
        assert status == 200, f"the second call should have queued, not been refused: {status} {body}"
        t.join(120)
        assert first and first[0][0] == 200, first
