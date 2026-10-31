#!/usr/bin/env python3
"""Forward Codex Responses traffic while recording privacy-safe request shapes.

The proxy does not write prompt, assistant, reasoning or tool bodies. It records
canonical hashes, item kinds, roles, lengths, request metadata and response
usage. A compaction request is compared with the previous ordinary request in
the same Codex conversation so the first model-visible divergence is explicit.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import pathlib
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import Any


HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def forward_headers(source: Any, body_length: int | None) -> dict[str, str]:
    """Copy end-to-end headers with one case-insensitive Content-Length."""
    headers = {
        key: value
        for key, value in source.items()
        if key.lower() not in HOP_BY_HOP
        and key.lower() not in ("host", "content-length")
    }
    if body_length is not None:
        headers["Content-Length"] = str(body_length)
    return headers


DIET_MIN_CHARS = 4000
DIET_KEEP_RECENT = 6
ERROR_MARKERS = (
    "traceback (most recent call last)",
    "error:",
    "error ",
    "failed",
    "failure",
    "panic",
    "exception",
    "permission denied",
    "command not found",
    "timed out",
    "timeout",
)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def text_fingerprint(value: Any) -> dict | None:
    if not isinstance(value, str):
        return None
    return {"chars": len(value), "sha256": digest(value)}


def content_shape(content: Any) -> list[dict]:
    if not isinstance(content, list):
        return []
    rows = []
    for part in content:
        if not isinstance(part, dict):
            rows.append({"kind": type(part).__name__, "sha256": digest(part)})
            continue
        row = {"kind": part.get("type") or "(none)"}
        if isinstance(part.get("text"), str):
            row["chars"] = len(part["text"])
        row["sha256"] = digest(part)
        rows.append(row)
    return rows


def item_shape(item: Any, index: int) -> dict:
    if not isinstance(item, dict):
        return {
            "index": index,
            "kind": type(item).__name__,
            "sha256": digest(item),
        }
    row = {
        "index": index,
        "kind": item.get("type") or "(none)",
        "sha256": digest(item),
    }
    for key in ("role", "status", "name", "namespace", "phase"):
        value = item.get(key)
        if isinstance(value, str):
            row[key] = value
    content = content_shape(item.get("content"))
    if content:
        row["content"] = content
    if isinstance(item.get("id"), str):
        row["id_sha256"] = digest(item["id"])
    return row


def codex_turn(req: dict) -> dict:
    metadata = req.get("client_metadata")
    raw = metadata.get("x-codex-turn-metadata") if isinstance(metadata, dict) else None
    try:
        turn = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        turn = None
    if not isinstance(turn, dict):
        return {"request_kind": None, "conversation_sha256": None}
    conversation = [turn.get("session_id"), turn.get("thread_id")]
    stable = all(isinstance(value, str) and value for value in conversation)
    return {
        "request_kind": turn.get("request_kind"),
        "thread_source": turn.get("thread_source"),
        "conversation_sha256": digest(conversation) if stable else None,
    }


def failed_tool_output(output: str) -> bool:
    """Fail open: retain anything that may contain an error or incomplete result."""
    try:
        parsed = json.loads(output)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        code = parsed.get("exit_code", parsed.get("exitCode"))
        if isinstance(code, int) and not isinstance(code, bool) and code != 0:
            return True
        if parsed.get("success") is False or parsed.get("error") is not None:
            return True
    lowered = output.lower()
    return any(marker in lowered for marker in ERROR_MARKERS)


class ContextDiet:
    """Persist old large successful outputs and replace them with exact references."""

    def __init__(
        self,
        root: pathlib.Path,
        min_chars: int = DIET_MIN_CHARS,
        keep_recent: int = DIET_KEEP_RECENT,
    ):
        self.root = pathlib.Path(root)
        if self.root.is_symlink():
            raise ValueError("context-diet directory must not be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("context-diet directory is not a directory")
        os.chmod(self.root, 0o700)
        self.min_chars = min_chars
        self.keep_recent = keep_recent
        self.lock = threading.Lock()

    def _artifact(self, conversation: str, call_id: str, output: str) -> pathlib.Path:
        name = f"{conversation[:24]}-{digest(call_id)[:24]}-{len(output)}-{digest(output)[:12]}.txt"
        path = self.root / name
        flags = os.O_WRONLY | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags | os.O_EXCL, 0o600)
        except FileExistsError:
            if path.is_symlink() or not path.is_file():
                raise ValueError("context-diet artifact is not a regular file") from None
            if path.read_bytes() != output.encode("utf-8"):
                raise ValueError("context-diet artifact hash collision")
            return path
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(output.encode("utf-8"))
        os.chmod(path, 0o600)
        return path

    def transform(self, req: dict) -> tuple[dict, dict]:
        turn = codex_turn(req)
        conversation = turn.get("conversation_sha256")
        items = req.get("input")
        if not conversation or not isinstance(items, list):
            return req, {"candidates": 0, "replaced": 0, "saved_chars": 0}

        output_indexes = [
            index
            for index, item in enumerate(items)
            if isinstance(item, dict)
            and item.get("type") in ("function_call_output", "custom_tool_call_output")
            and isinstance(item.get("output"), str)
        ]
        keep = (
            set(output_indexes[-self.keep_recent :])
            if self.keep_recent
            else set()
        )
        changed = False
        replaced = 0
        saved = 0
        transformed = []
        with self.lock:
            for index, item in enumerate(items):
                if (
                    index not in keep
                    and isinstance(item, dict)
                    and item.get("type") in ("function_call_output", "custom_tool_call_output")
                    and isinstance(item.get("output"), str)
                ):
                    output = item["output"]
                    if len(output) >= self.min_chars and not failed_tool_output(output):
                        call_id = item.get("call_id")
                        if isinstance(call_id, str) and call_id:
                            path = self._artifact(conversation, call_id, output)
                            reference = (
                                "[Strata context diet: full tool output is saved unchanged locally.\n"
                                f"bytes={len(output.encode('utf-8'))} sha256={digest(output)}\n"
                                f"path={path}\n"
                                f"read: sed -n '1,200p' {path}\n"
                                f"search: rg -n 'PATTERN' {path}\n"
                                "Use the file when older details are required.]"
                            )
                            transformed.append({**item, "output": reference})
                            changed = True
                            replaced += 1
                            saved += len(output) - len(reference)
                            continue
                transformed.append(item)
        if not changed:
            return req, {"candidates": len(output_indexes), "replaced": 0, "saved_chars": 0}
        return {**req, "input": transformed}, {
            "candidates": len(output_indexes),
            "replaced": replaced,
            "saved_chars": saved,
            "min_chars": self.min_chars,
            "keep_recent": self.keep_recent,
        }


def request_shape(req: dict) -> dict:
    tools = req.get("tools")
    input_items = req.get("input")
    if not isinstance(input_items, list):
        input_items = []
    tool_types = sorted(
        tool.get("type")
        for tool in tools or []
        if isinstance(tool, dict) and isinstance(tool.get("type"), str)
    )
    tool_names = sorted(
        name
        for tool in tools or []
        if isinstance(tool, dict)
        for name in [tool.get("name")]
        if isinstance(name, str)
    )
    return {
        "at": round(time.time(), 3),
        "method": "POST",
        "path": "/v1/responses",
        "request_sha256": digest(req),
        **codex_turn(req),
        "model": req.get("model"),
        "stream": req.get("stream"),
        "max_output_tokens": req.get("max_output_tokens"),
        "sample": {
            key: req.get(key)
            for key in ("temperature", "top_p", "top_k", "seed")
            if req.get(key) is not None
        },
        "instructions": text_fingerprint(req.get("instructions")),
        "tools": {
            "count": len(tools) if isinstance(tools, list) else 0,
            "types": tool_types,
            "names": tool_names,
            "sha256": digest(tools),
        },
        "input": {
            "count": len(input_items),
            "sha256": digest(input_items),
            "items": [item_shape(item, index) for index, item in enumerate(input_items)],
        },
    }


def first_divergence(previous: dict, current: dict) -> dict:
    """Compare request fields in model-rendering order without exposing content."""
    previous_tools = previous.get("tools") or {}
    current_tools = current.get("tools") or {}
    previous_instructions = previous.get("instructions")
    current_instructions = current.get("instructions")
    if previous_instructions != current_instructions:
        return {
            "location": "instructions",
            "previous_sha256": (previous_instructions or {}).get("sha256"),
            "current_sha256": (current_instructions or {}).get("sha256"),
            "common_prefix_tokens_known": False,
        }
    if previous_tools.get("sha256") != current_tools.get("sha256"):
        return {
            "location": "tools",
            "previous_count": previous_tools.get("count", 0),
            "current_count": current_tools.get("count", 0),
            "previous_sha256": previous_tools.get("sha256"),
            "current_sha256": current_tools.get("sha256"),
        }
    old_items = ((previous.get("input") or {}).get("items") or [])
    new_items = ((current.get("input") or {}).get("items") or [])
    common = 0
    for old, new in zip(old_items, new_items):
        if old.get("sha256") != new.get("sha256"):
            break
        common += 1
    if common == len(old_items) and len(new_items) >= common:
        return {
            "location": "input_append",
            "item_index": common,
            "common_items": common,
            "previous_items": len(old_items),
            "current_items": len(new_items),
        }
    old = old_items[common] if common < len(old_items) else {}
    new = new_items[common] if common < len(new_items) else {}
    return {
        "location": "input_item",
        "item_index": common,
        "common_items": common,
        "previous_kind": old.get("kind"),
        "current_kind": new.get("kind"),
        "previous_role": old.get("role"),
        "current_role": new.get("role"),
        "previous_sha256": old.get("sha256"),
        "current_sha256": new.get("sha256"),
    }


def response_summary(raw: bytes, content_type: str) -> dict:
    """Extract status and usage, never model text."""
    summary: dict[str, Any] = {}
    candidates = []
    if "text/event-stream" in content_type:
        for line in raw.decode("utf-8", "replace").splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            if isinstance(event, dict):
                candidates.append(event)
    else:
        try:
            event = json.loads(raw)
        except ValueError:
            event = None
        if isinstance(event, dict):
            candidates.append(event)
    for event in reversed(candidates):
        response = event.get("response")
        if not isinstance(response, dict):
            continue
        usage = response.get("usage")
        if isinstance(usage, dict):
            input_details = usage.get("input_tokens_details")
            summary["usage"] = {
                "input_tokens": usage.get("input_tokens"),
                "cached_tokens": input_details.get("cached_tokens")
                if isinstance(input_details, dict)
                else None,
                "output_tokens": usage.get("output_tokens"),
            }
        if isinstance(response.get("status"), str):
            summary["status"] = response["status"]
        if isinstance(response.get("incomplete_details"), dict):
            summary["incomplete_details"] = response["incomplete_details"]
        if summary:
            break
    return summary


class ShapeRecorder:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.lock = threading.Lock()
        self.previous_ordinary: dict[str, dict] = {}

    def record(self, shape: dict, summary: dict | None = None) -> dict:
        conversation = shape.get("conversation_sha256")
        if shape.get("request_kind") == "compaction" and conversation:
            previous = self.previous_ordinary.get(conversation)
            shape["comparison"] = (
                first_divergence(previous, shape)
                if previous is not None
                else {"location": "previous_ordinary_missing"}
            )
        if summary:
            shape["response"] = summary
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(shape, sort_keys=True, separators=(",", ":")) + "\n")
            if shape.get("request_kind") != "compaction" and conversation:
                self.previous_ordinary[conversation] = shape
        return shape


class ShapeProxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    recorder: ShapeRecorder
    upstream: urllib.parse.SplitResult
    diet: ContextDiet | None = None

    def log_message(self, fmt, *args):
        return

    def _upstream_connection(self):
        cls = (
            http.client.HTTPSConnection
            if self.upstream.scheme == "https"
            else http.client.HTTPConnection
        )
        return cls(self.upstream.hostname, self.upstream.port, timeout=900)

    def _forward(self, method: str):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = None
        shape = None
        if method == "POST" and self.path.split("?", 1)[0].rstrip("/") == "/v1/responses":
            try:
                req = json.loads(body or b"{}")
            except ValueError:
                req = None
            if isinstance(req, dict):
                original_request_sha256 = digest(req)
                if self.diet is not None:
                    req, diet_summary = self.diet.transform(req)
                    if diet_summary["replaced"]:
                        body = json.dumps(
                            req, ensure_ascii=False, separators=(",", ":")
                        ).encode("utf-8")
                    shape = request_shape(req)
                    shape["original_request_sha256"] = original_request_sha256
                    shape["context_diet"] = diet_summary
                else:
                    shape = request_shape(req)

        base_path = self.upstream.path.rstrip("/")
        target = base_path + self.path
        headers = forward_headers(
            self.headers, len(body) if body is not None else None
        )
        connection = self._upstream_connection()
        started = time.monotonic()
        try:
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in HOP_BY_HOP:
                    self.send_header(key, value)
            if response.getheader("Content-Length") is None:
                self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            self.wfile.flush()
            if shape is not None:
                summary = response_summary(raw, response.getheader("Content-Type") or "")
                summary["duration_s"] = round(time.monotonic() - started, 3)
                summary["http_status"] = response.status
                self.recorder.record(shape, summary)
        except (OSError, http.client.HTTPException) as exc:
            if shape is not None:
                self.recorder.record(
                    shape,
                    {
                        "http_status": 502,
                        "error_type": type(exc).__name__,
                        "duration_s": round(time.monotonic() - started, 3),
                    },
                )
            try:
                self.send_error(502, "upstream request failed")
            except OSError:
                pass
        finally:
            connection.close()

    def do_GET(self):
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", default="http://127.0.0.1:18080")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18082)
    parser.add_argument("--log", type=pathlib.Path, required=True)
    parser.add_argument(
        "--context-diet-dir",
        type=pathlib.Path,
        help="opt in: save old large successful tool outputs here and send references",
    )
    parser.add_argument("--context-diet-min-chars", type=int, default=DIET_MIN_CHARS)
    parser.add_argument(
        "--context-diet-keep-recent",
        type=int,
        default=DIET_KEEP_RECENT,
        help="keep this many newest outputs verbatim; 0 folds all eligible outputs",
    )
    args = parser.parse_args()

    upstream = urllib.parse.urlsplit(args.upstream)
    if upstream.scheme not in ("http", "https") or not upstream.hostname:
        parser.error("--upstream must be an http(s) URL")
    if args.context_diet_min_chars < 1:
        parser.error("--context-diet-min-chars must be positive")
    if args.context_diet_keep_recent < 0:
        parser.error("--context-diet-keep-recent must be non-negative")
    ShapeProxy.upstream = upstream
    ShapeProxy.recorder = ShapeRecorder(args.log)
    try:
        ShapeProxy.diet = (
            ContextDiet(
                args.context_diet_dir,
                args.context_diet_min_chars,
                args.context_diet_keep_recent,
            )
            if args.context_diet_dir
            else None
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    server = ThreadingHTTPServer((args.bind, args.port), ShapeProxy)
    print(
        f"shape proxy {args.bind}:{args.port} -> {args.upstream}; "
        f"privacy-safe log {args.log}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
