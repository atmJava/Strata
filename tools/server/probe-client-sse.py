#!/usr/bin/env python3
"""Measure what one SSE client receives and join it to the server's request trace.

The probe sends one small streaming request, timestamps each SSE data line as it
arrives at the client, and then reads the matching request by X-Request-ID from
``/metrics?requests=all``.  It compares delta counts and maps the first and last
client deltas to the engine token recorded beside the same server delta.

The measurement stops at the HTTP client.  It proves that the client received a
delta, not that an application rendered or acknowledged it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = os.environ.get("MP_BASE_URL", "http://127.0.0.1:8080")
DEFAULT_API_KEY = os.environ.get("MP_API_KEY", "111")
DEFAULT_MODEL = os.environ.get("MP_MODEL", "qwen3.8-flash-next-iq3_xxs")
API_PATHS = {
    "chat": "/v1/chat/completions",
    "responses": "/v1/responses",
    "anthropic": "/v1/messages",
}
CLIENT_TRACE_MAX_EVENTS = 512


class ProbeError(RuntimeError):
    """The request, SSE parse, or server-side request join was not usable."""


def utc_now() -> str:
    """Return a UTC timestamp with millisecond precision."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def request_body(api: str, model: str, prompt: str, max_tokens: int,
                 reasoning_effort: str | None) -> dict[str, Any]:
    """Build the smallest normal streaming request for one supported API."""
    if api == "chat":
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": True,
        }
    elif api == "responses":
        body = {
            "model": model,
            "input": prompt,
            "max_output_tokens": max_tokens,
            "temperature": 0,
            "stream": True,
        }
    elif api == "anthropic":
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": True,
        }
    else:
        raise ValueError(f"unsupported API: {api}")
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    return body


def visible_chars(api: str, payload: dict[str, Any]) -> int:
    """Return the user-visible text length in one parsed SSE data object."""
    if api == "chat":
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return 0
        delta = choices[0].get("delta")
        if not isinstance(delta, dict):
            return 0
        return sum(len(value) for key in ("content", "reasoning_content")
                   if isinstance((value := delta.get(key)), str) and value)
    if api == "responses":
        if payload.get("type") not in ("response.output_text.delta", "response.reasoning_text.delta"):
            return 0
        delta = payload.get("delta")
        return len(delta) if isinstance(delta, str) and delta else 0
    if payload.get("type") == "content_block_delta":
        delta = payload.get("delta")
        if not isinstance(delta, dict):
            return 0
        return sum(len(value) for key in ("text", "thinking", "partial_json")
                   if isinstance((value := delta.get(key)), str) and value)
    return 0


def _headers(api_key: str) -> dict[str, str]:
    result = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        result["Authorization"] = "Bearer " + api_key
        result["x-api-key"] = api_key
    result["anthropic-version"] = "2023-06-01"
    return result


def capture_stream(base_url: str, api: str, body: dict[str, Any], api_key: str,
                   timeout_s: float) -> tuple[str | None, dict[str, Any]]:
    """Read one SSE response and retain bounded timing and delta metadata."""
    path = API_PATHS[api]
    request = urllib.request.Request(base_url.rstrip("/") + path,
                                     data=json.dumps(body).encode("utf-8"),
                                     headers=_headers(api_key), method="POST")
    started = time.monotonic()
    events: list[dict[str, Any]] = []
    data_events = 0
    keepalive_comments = 0
    visible_delta_events = 0
    cumulative_visible_chars = 0
    done_seen = False
    timeline_truncated = False
    event_name: str | None = None
    first_visible = None
    last_visible = None

    def remember(item: dict[str, Any]) -> None:
        """Keep a bounded event sequence while counts and first/last timings remain exact."""
        nonlocal timeline_truncated
        events.append(item)
        if len(events) > CLIENT_TRACE_MAX_EVENTS:
            del events[:CLIENT_TRACE_MAX_EVENTS // 2]
            timeline_truncated = True

    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        request_id = response.headers.get("X-Request-ID")
        response_started_ms = round((time.monotonic() - started) * 1000.0, 3)
        while True:
            raw = response.readline()
            received_at = time.monotonic()
            if raw == b"":
                break
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                event_name = None
                continue
            if line.startswith(":"):
                keepalive_comments += 1
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip() or None
                continue
            if not line.startswith("data:"):
                continue
            data_events += 1
            data = line[5:].strip()
            elapsed_ms = round((received_at - started) * 1000.0, 3)
            if data == "[DONE]":
                done_seen = True
                remember({"data_index": data_events, "kind": "done",
                          "received_ms": elapsed_ms, "data_line_bytes": len(raw)})
                break
            try:
                payload = json.loads(data)
            except json.JSONDecodeError as exc:
                raise ProbeError(f"data frame {data_events} is not JSON: {exc}") from None
            if not isinstance(payload, dict):
                raise ProbeError(f"data frame {data_events} is not a JSON object")
            chars = visible_chars(api, payload)
            item: dict[str, Any] = {
                "data_index": data_events,
                "kind": event_name or str(payload.get("type") or "message"),
                "received_ms": elapsed_ms,
                "data_line_bytes": len(raw),
                "visible_chars": chars,
            }
            if chars:
                visible_delta_events += 1
                cumulative_visible_chars += chars
                item.update(delta_index=visible_delta_events,
                            cumulative_visible_chars=cumulative_visible_chars)
                first_visible = first_visible or item
                last_visible = item
            remember(item)
    ended_ms = round((time.monotonic() - started) * 1000.0, 3)
    return request_id, {
        "measurement_side": "client_http_read_not_render",
        "client_received": True,
        "client_rendered": False,
        "client_ack": {
            "available": False,
            "reason": "SSE has no application acknowledgement",
        },
        "request_started_mono": started,
        "response_headers_ms": response_started_ms,
        "stream_ended_ms": ended_ms,
        "data_events": data_events,
        "keepalive_comments": keepalive_comments,
        "visible_delta_events": visible_delta_events,
        "visible_chars": cumulative_visible_chars,
        "done_frame_seen": done_seen,
        "first_visible_delta_ms": first_visible["received_ms"] if first_visible else None,
        "last_visible_delta_ms": last_visible["received_ms"] if last_visible else None,
        "first_delta_data_line_bytes": first_visible["data_line_bytes"] if first_visible else None,
        "last_delta_data_line_bytes": last_visible["data_line_bytes"] if last_visible else None,
        "dynamic_event_limit": CLIENT_TRACE_MAX_EVENTS,
        "dynamic_events_truncated": timeline_truncated,
        "event_timeline": events,
    }


def fetch_server_observation(base_url: str, api_key: str, request_id: str,
                             timeout_s: float, settle_s: float) -> tuple[dict[str, Any] | None, str | None]:
    """Read the matching request ledger row, waiting briefly for its final trace."""
    deadline = time.monotonic() + max(0.0, settle_s)
    last_error = None
    while True:
        request = urllib.request.Request(base_url.rstrip("/") + "/metrics?requests=all",
                                         headers=_headers(api_key))
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                payload = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            payload = None
        rows = payload.get("requests") if isinstance(payload, dict) else None
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("id") == request_id:
                    observation = row.get("instrumentation")
                    if isinstance(observation, dict) and observation.get("sse") is not None:
                        return row, None
        if time.monotonic() >= deadline:
            return None, last_error or "request trace not retained"
        time.sleep(0.05)


def join_client_and_server(client: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Join client deltas to server deltas by delta index and report mapping quality."""
    observation = row.get("instrumentation") if isinstance(row.get("instrumentation"), dict) else {}
    server = observation.get("sse") if isinstance(observation.get("sse"), dict) else {}
    timeline = [event for event in server.get("event_timeline") or []
                if isinstance(event, dict) and event.get("delta_index")]
    by_delta = {event["delta_index"]: event for event in timeline if type(event.get("delta_index")) is int}
    client_visible = [event for event in client.get("event_timeline") or []
                      if type(event.get("delta_index")) is int]
    matched = []
    missing_source = []
    for event in client_visible:
        delta_index = event["delta_index"]
        server_event = by_delta.get(delta_index)
        if server_event is None:
            continue
        source = server_event.get("source")
        if isinstance(source, dict) and type(source.get("token_index")) is int:
            matched.append({"delta_index": delta_index,
                            "client_received_ms": event.get("received_ms"),
                            "source": source})
        else:
            missing_source.append(delta_index)
    server_count = int(server.get("visible_delta_events") or 0)
    client_count = int(client.get("visible_delta_events") or 0)
    truncated = bool((server.get("chunk_to_delta") or {}).get("timeline_truncated"))
    client_truncated = bool(client.get("dynamic_events_truncated"))
    exact = (not truncated and not client_truncated and client_count == server_count and len(matched) == client_count
             and not missing_source)
    return {
        "client_visible_delta_events": client_count,
        "server_visible_delta_events": server_count,
        "server_timeline_visible_delta_events": len(timeline),
        "server_timeline_truncated": truncated,
        "client_timeline_truncated": client_truncated,
        "matched_delta_events": len(matched),
        "missing_engine_source": missing_source,
        "exact_when_retained": exact,
        "basis": "delta_index joins one client SSE data frame to one server-side API delta",
        "first_client_delta_ms": client.get("first_visible_delta_ms"),
        "last_client_delta_ms": client.get("last_visible_delta_ms"),
        "server_first_visible_delta_ms": server.get("first_visible_delta_ms"),
        "server_last_visible_delta_ms": server.get("last_visible_delta_ms"),
        "first_mapped_source": matched[0]["source"] if matched else None,
        "last_mapped_source": matched[-1]["source"] if matched else None,
        "clock_note": "client and server elapsed values are measured in separate processes; "
                      "counts, order and delta_index are the cross-process join",
    }


def run_probe(base_url: str, api: str, body: dict[str, Any], api_key: str,
              timeout_s: float = 60.0, settle_s: float = 2.0) -> dict[str, Any]:
    """Run one client capture and join it to the matching server request."""
    request_id, client = capture_stream(base_url, api, body, api_key, timeout_s)
    if not request_id:
        raise ProbeError("the SSE response has no X-Request-ID header")
    row, join_error = fetch_server_observation(base_url, api_key, request_id, timeout_s, settle_s)
    report: dict[str, Any] = {
        "schema_version": 1,
        "collected_at_utc": utc_now(),
        "source": base_url.rstrip("/") + API_PATHS[api],
        "request_id": request_id,
        "api": api,
        "request_body_sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
        "client": {key: value for key, value in client.items() if key != "request_started_mono"},
    }
    if row is None:
        report["server_join"] = {"available": False, "reason": join_error}
        return report
    report["server_join"] = {"available": True, **join_client_and_server(client, row)}
    return report


def _write_report(report: dict[str, Any], output: Path | None) -> None:
    raw = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output is None:
        sys.stdout.write(raw)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(raw, encoding="utf-8", newline="\n")
    os.replace(temporary, output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api", choices=sorted(API_PATHS), default="responses")
    parser.add_argument("--prompt", default="Reply with one short sentence.")
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--reasoning-effort", default="none")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--settle-timeout", type=float, default=2.0)
    parser.add_argument("--out", type=Path)
    options = parser.parse_args(argv)
    if options.max_tokens <= 0:
        parser.error("--max-tokens must be positive")
    body = request_body(options.api, options.model, options.prompt, options.max_tokens,
                        None if options.reasoning_effort == "none" else options.reasoning_effort)
    try:
        report = run_probe(options.base_url, options.api, body, options.api_key,
                           options.timeout, options.settle_timeout)
    except (ProbeError, OSError, urllib.error.URLError) as exc:
        print(f"client-sse: {exc}", file=sys.stderr)
        return 2
    _write_report(report, options.out)
    server_join = report.get("server_join") or {}
    return 0 if server_join.get("available") and server_join.get("exact_when_retained") else 1


if __name__ == "__main__":
    raise SystemExit(main())
