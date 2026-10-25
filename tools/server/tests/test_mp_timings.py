"""S2: a request's own timings in the response - queue, first token, inter-token latency.

The engine's `timings` (llama.cpp's names) say how fast the read and the decode were; they do not say how long the
request waited for a slot or how long the client waited for its first token.  `done["metrics"]`
(`serve/server.py`, the S2 block) puts those on the request's last chunk (and on the non-streamed body), read from
the request's own row: `queue_ms <= ttft_ms <= wall_ms` must hold, and `itl_ms_mean` exists once more than one
token came.  Mock engine offline: no GPU, no production.
"""
import contextlib
import json
import socket
import subprocess
import sys
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


def _body(stream: bool) -> bytes:
    return json.dumps({"messages": [{"role": "user", "content": "hi"}], "max_tokens": 20,
                       "stream": stream}).encode()


def _check(metrics: dict, tokens: int) -> None:
    assert isinstance(metrics, dict), metrics
    for key in ("queue_ms", "ttft_ms", "wall_ms"):
        assert isinstance(metrics[key], int), (key, metrics)
        assert metrics[key] >= 0, (key, metrics)
    assert metrics["queue_ms"] <= metrics["ttft_ms"], metrics
    assert metrics["ttft_ms"] <= metrics["wall_ms"], metrics
    assert metrics["tokens"] == tokens, metrics
    assert tokens > 1 and metrics["itl_ms_mean"] is not None, metrics
    assert metrics["itl_ms_mean"] > 0, metrics
    if metrics.get("slot_wait_ms") is not None:
        assert isinstance(metrics["slot_wait_ms"], int), metrics
        assert metrics["queue_ms"] <= metrics["slot_wait_ms"] <= metrics["ttft_ms"], metrics


def test_streamed_final_chunk_carries_metrics():
    with _serve() as port:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=_body(True),
                                         headers={"Content-Type": "application/json"})
        last = None
        with urllib.request.urlopen(request, timeout=120) as response:
            for raw in response:
                line = raw.decode().strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("usage"):
                    last = chunk
        assert last is not None and "metrics" in last, last
        print(f"  S2 streamed: {last['metrics']}")
        _check(last["metrics"], last["usage"]["completion_tokens"])
        assert last["metrics"]["ttft_ms"] < last["metrics"]["wall_ms"], last["metrics"]


def test_non_streamed_body_carries_metrics():
    with _serve() as port:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=_body(False),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read())
        assert "metrics" in body, body.keys()
        print(f"  S2 non-streamed: {body['metrics']}")
        _check(body["metrics"], body["usage"]["completion_tokens"])
