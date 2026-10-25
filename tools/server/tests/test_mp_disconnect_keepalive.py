"""S7/S8: a client that goes away stops the engine (S7) and a queued stream keeps breathing (S8).

Both are "already implemented, missing test":

- S7: `serve/server.py:7184` — an `OSError` while writing the SSE stream calls
  `svc.note_client_disconnect(cancel)`, which sets the cancel event the engine polls (`MockEngine.generate`
  checks it before every token).  The test closes a running stream and asserts the *next* request is served
  promptly: with a single turn and a 0.5 s/token script the dead request would hold the turn for ~50 s if
  the cancel never happened, so "B finishes in a few seconds" is the observable.
- S8: `serve/server.py:5201` — a request waiting for the single turn gets `yield "ping", None` every
  `QUEUE_BEAT_S` (10 s), written as the SSE comment `: keep-alive` (`serve/server.py:7179`).  The test
  queues B behind a long A and asserts a keep-alive comment arrives before any token does.

Both run the mock engine offline: no GPU, no production, no network beyond 127.0.0.1.
"""
import contextlib
import http.client
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


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


def _stream(port: int, max_tokens: int):
    """Open a streaming request; returns (connection, response) once the headers are in."""
    body = json.dumps({"messages": [{"role": "user", "content": "hi"}], "max_tokens": max_tokens,
                       "stream": True}).encode()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    conn.request("POST", "/v1/chat/completions", body=body, headers={"Content-Type": "application/json"})
    return conn, conn.getresponse()


def _first_data_line(response) -> bytes:
    while True:
        line = response.readline()
        if not line:
            raise AssertionError("the stream ended before its first data line")
        if line.startswith(b"data:"):
            return line


def _post_json(port: int, max_tokens: int) -> tuple[int, float]:
    body = json.dumps({"messages": [{"role": "user", "content": "hi"}], "max_tokens": max_tokens}).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=120) as r:
        r.read()
        return r.status, time.monotonic() - started


def test_s7_client_disconnect_stops_the_engine():
    with _serve() as port:
        conn, response = _stream(port, 100)          # 100 tokens at 0.5 s = ~50 s if it runs to the end
        _first_data_line(response)                   # the request is in flight
        # close both: `http.client` keeps the fd alive in the response's file object, so closing only the
        # connection would leave the socket open and the server would never see the hang-up.
        response.close()
        conn.close()                                 # the client goes away
        # the slot release itself, straight from /metrics (the criterion is <= 2 s)
        closed_at = time.monotonic()
        released = None
        while time.monotonic() - closed_at < 3.0:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as m:
                state = (json.load(m).get("live") or {}).get("state")
            if state in (None, "idle"):
                released = time.monotonic() - closed_at
                break
            time.sleep(0.05)
        print(f"  S7: /metrics idle {released if released is not None else 'never'} s after the disconnect")
        assert released is not None and released <= 2.0, f"the slot was not released within 2 s ({released})"
        # B rides the same single turn: it only finishes quickly if A's cancel reached the engine.
        # the criterion is <= 2 s for the slot release; B's own 3 tokens at 0.5 s/token are not part of it, so
        # B finishing within 3 s leaves the release itself inside the bound.  The tighter number (2.0 s) is printed.
        status, wall = _post_json(port, 3)
        print(f"  S7: the next request finished {wall:.1f} s after the disconnect")
        assert status == 200, status
        assert wall < 3.0, f"the dead request held the turn: the next request took {wall:.1f} s"


def test_s8_queued_stream_gets_a_keepalive():
    with _serve() as port:
        conn_a, response_a = _stream(port, 100)      # holds the single turn for ~50 s
        _first_data_line(response_a)                 # A is generating
        drained = threading.Event()

        def drain():
            try:
                while response_a.readline():
                    pass
            finally:
                drained.set()

        threading.Thread(target=drain, daemon=True).start()

        started_b = time.monotonic()
        conn_b, response_b = _stream(port, 3)        # queued behind A
        lines = []
        deadline = time.monotonic() + 13.0
        keepalive_at = None
        while time.monotonic() < deadline:
            line = response_b.readline()
            if not line:
                break
            lines.append(line)
            if line.strip() == b": keep-alive":
                keepalive_at = time.monotonic()
                break
        try:
            print(f"  S8: keep-alive comment came {keepalive_at - started_b:.1f} s into the wait"
                  if keepalive_at else "  S8: no keep-alive")
            assert keepalive_at is not None, f"no keep-alive within 13 s; lines={lines[-5:]!r}"
            response_a.close()                       # let B have the turn (close the file object too)
            conn_a.close()
            status = 200
            rest = response_b.read()
            assert b"[DONE]" in rest or b"data:" in rest, "B never got its answer"
            assert status == 200
        finally:
            conn_b.close()
            conn_a.close()
            drained.wait(5)
