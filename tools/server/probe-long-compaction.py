#!/usr/bin/env python3
"""Measure a long Codex conversation turn followed by its compaction.

The probe sends one deterministic history turn and then the same conversation
with Codex's compaction metadata and ``tools: []``.  It reports token counts,
cache reuse, walls and status, but never writes prompt or response text.

The optional post-compaction branch mode is diagnostic. It does not model the
user's separate "reopen a conversation last used more than a day ago" case.

Example:
    python3 tools/server/probe-long-compaction.py \
      --config /path/to/strata.json \
      --history-words 145000 \
      --output /opt/ai/logs/agent-compaction-210k.json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import time
import urllib.request


DEFAULT_MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"
WORDS = (
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
    "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
    "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform",
    "victor", "whiskey", "xray", "yankee", "zulu", "pipeline", "report",
    "state", "owner", "deadline", "checkpoint",
)
COMPACT_PROMPT = "You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary."
TOOLS = [{
    "type": "function",
    "name": "exec_command",
    "description": "Run a read-only shell command in the project.",
    "parameters": {
        "type": "object",
        "properties": {"cmd": {"type": "string"}},
        "required": ["cmd"],
    },
}]


def api_key(config: pathlib.Path | None, env_name: str) -> str:
    if config:
        try:
            value = json.loads(config.read_text(encoding="utf-8")).get("api_key")
            if isinstance(value, str) and value:
                return value
        except (OSError, ValueError):
            pass
    return os.environ.get(env_name, "")


def history_text(words: int, tag: str) -> str:
    if words < 1:
        raise ValueError("history words must be positive")
    body = " ".join(WORDS[index % len(WORDS)] for index in range(words))
    return f"Project history {tag}:\n{body}"


def turn_meta(session: str, thread: str, kind: str) -> dict:
    conversation = {"session_id": session, "thread_id": thread, "request_kind": kind}
    return {
        "session_id": session,
        "thread_id": thread,
        "x-codex-turn-metadata": json.dumps(conversation, separators=(",", ":")),
    }


def turn_body(model: str, text: str, session: str, thread: str, output_tokens: int = 16,
              pin_chars: int | None = None, pin_tokens: int | None = None) -> dict:
    if pin_chars is not None and pin_tokens is not None:
        raise ValueError("choose pin_chars or pin_tokens, not both")
    body = {
        "model": model,
        "instructions": "You are a coding agent. Read the archive and answer briefly.",
        "input": [{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text + "\n\nReply with exactly READY."}],
        }],
        "tools": TOOLS,
        "tool_choice": "none",
        "parallel_tool_calls": False,
        "prompt_cache_key": f"probe-long-compaction-{session}",
        "client_metadata": turn_meta(session, thread, "turn"),
        "max_output_tokens": output_tokens,
        "temperature": 0,
    }
    if pin_chars is not None:
        if pin_chars < 1 or pin_chars >= len(text) + len("\n\nReply with exactly READY."):
            raise ValueError("pin_chars must leave part of the user message unpinned")
        # turn_body also sends an ``instructions`` field, which becomes message 0.
        body["strata_prefix"] = {"message": 1, "chars": pin_chars}
    if pin_tokens is not None:
        if pin_tokens < 1:
            raise ValueError("pin_tokens must be positive")
        body["strata_prefix"] = {"tokens": pin_tokens}
    return body


def compaction_body(model: str, turn: dict, output: list, session: str, thread: str,
                    output_tokens: int = 256) -> dict:
    return {
        **turn,
        "input": turn["input"] + output + [{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": COMPACT_PROMPT}],
        }],
        "tools": [],
        "client_metadata": turn_meta(session, thread, "compaction"),
        "max_output_tokens": output_tokens,
    }


def post_json(url: str, key: str, body: dict, timeout: float) -> tuple[dict, float]:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers=headers,
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload, time.monotonic() - started


def response_row(payload: dict, wall_s: float) -> dict:
    usage = payload.get("usage") or {}
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    return {
        "input": usage.get("input_tokens", usage.get("prompt_tokens")),
        "cached": details.get("cached_tokens"),
        "output": usage.get("output_tokens", usage.get("completion_tokens")),
        "wall_s": round(wall_s, 6),
        "status": payload.get("status"),
        "finish": (payload.get("incomplete_details") or {}).get("reason"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--config", type=pathlib.Path)
    parser.add_argument("--api-key-env", default="STRATA_API_KEY")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--history-words", type=int, default=145_000)
    parser.add_argument("--turn-output-tokens", type=int, default=16)
    parser.add_argument("--compaction-output-tokens", type=int, default=256)
    parser.add_argument("--session", default=f"long-compaction-{time.time_ns()}")
    parser.add_argument("--thread", default="main")
    parser.add_argument("--pin-history", action="store_true",
                        help="pin the history/user-message boundary before the READY instruction")
    parser.add_argument("--pin-tokens", type=int,
                        help="pin this exact prompt-token prefix instead of resolving a character boundary")
    parser.add_argument("--branch-after-compaction", action="store_true",
                        help="repeat the original turn after compaction to test branch checkpoint retention; "
                             "this is not an aged-session resume test")
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.history_words < 1:
        parser.error("--history-words must be positive")

    key = api_key(args.config, args.api_key_env)
    url = args.base_url.rstrip("/") + "/v1/responses"
    text = history_text(args.history_words, args.session)
    turn = turn_body(args.model, text, args.session, args.thread, args.turn_output_tokens,
                     len(text) if args.pin_history and args.pin_tokens is None else None,
                     args.pin_tokens)
    turn_payload, turn_wall = post_json(url, key, turn, args.timeout)
    compact = compaction_body(args.model, turn, turn_payload.get("output") or [],
                              args.session, args.thread, args.compaction_output_tokens)
    compaction_payload, compaction_wall = post_json(url, key, compact, args.timeout)
    branch_payload = branch_wall = None
    if args.branch_after_compaction:
        branch_payload, branch_wall = post_json(url, key, turn, args.timeout)
    result = {
        "target": "long history then Codex compaction",
        "history_words": args.history_words,
        "turn_output_tokens": args.turn_output_tokens,
        "compaction_output_tokens": args.compaction_output_tokens,
        "turn": response_row(turn_payload, turn_wall),
        "compaction": response_row(compaction_payload, compaction_wall),
    }
    if branch_payload is not None:
        result["branch"] = response_row(branch_payload, branch_wall)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
