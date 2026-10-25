"""Shared HTTP helpers for the black-box acceptance tests (T1-T4).

Only the standard library: these tests run against the live server from any machine
that can reach it, with no engine internals and no GPU.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("MP_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
API_KEY = os.environ.get("MP_API_KEY", "111")
MODEL = os.environ.get("MP_MODEL", "qwen3.8-flash-next-iq3_xxs")


def headers(auth: bool = True) -> dict[str, str]:
    result = {"Content-Type": "application/json"}
    if auth and API_KEY:
        result["Authorization"] = "Bearer " + API_KEY
    return result


def _open(path: str, body: dict | None, auth: bool, timeout: float):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers(auth),
                                 method="POST" if data is not None else "GET")
    return urllib.request.urlopen(req, timeout=timeout)


def get_json(path: str, auth: bool = True, timeout: float = 30) -> dict:
    with _open(path, None, auth, timeout) as response:
        return json.loads(response.read().decode())


def post_json(path: str, body: dict, auth: bool = True, timeout: float = 900) -> dict:
    with _open(path, body, auth, timeout) as response:
        return json.loads(response.read().decode())


def chat_body(prompt: str, max_tokens: int = 32, stream: bool = False,
              temperature: float = 0, effort: str | None = "none") -> dict:
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": temperature, "stream": stream}
    if effort is not None:
        body["reasoning_effort"] = effort    # "none": no thinking tokens inside max_tokens (L-009/L-026)
    return body


def stream_chat(body: dict, auth: bool = True, timeout: float = 900):
    """Yields (event_json, seconds_since_request) for each `data: {...}` line until [DONE]."""
    started = time.monotonic()
    with _open("/v1/chat/completions", body, auth, timeout) as response:
        for raw in response:
            line = raw.decode(errors="replace").strip()
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            yield json.loads(payload), time.monotonic() - started


def collect_stream(body: dict, auth: bool = True, timeout: float = 900):
    """Returns (text, usage_or_None, seconds_to_first_content_token)."""
    text: list[str] = []
    usage = None
    first = None
    for event, elapsed in stream_chat(body, auth=auth, timeout=timeout):
        got_content = False
        for choice in event.get("choices", []):
            delta = choice.get("delta") or {}
            if delta.get("content"):
                text.append(delta["content"])
                got_content = True
        if first is None and got_content:
            first = elapsed
        if event.get("usage"):
            usage = event["usage"]
    return "".join(text), usage, first
