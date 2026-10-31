#!/usr/bin/env python3
"""Measure agent-shaped conversations without changing or restarting the server.

Each level runs one to four independent conversations.  A conversation has a
shared instruction prefix, a session-specific context, and consecutive tool-like
turns.  This exercises the same prompt reuse and batched decode path as a main
agent with child agents while recording no prompt or response text.

The script is a probe: it never edits config, loads an engine, or restarts a
process.  Results contain request metadata only.

Example:
    STRATA_API_KEY=... probe-agent-sessions.py \
      --levels 1,2,4 --waves 6 --rounds 2 --context-tokens 8192 \
      --output /opt/ai/logs/agent-sessions.json
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import statistics
import threading
import time
import urllib.error
import urllib.request


DEFAULT_MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"
RUN_ID = f"{time.time_ns() % 1_000_000_000:09d}"
WORDS = (
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
    "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
    "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform",
    "victor", "whiskey", "xray", "yankee", "zulu", "pipeline", "report",
    "state", "owner", "deadline", "checkpoint",
)


def api_key(args: argparse.Namespace) -> str:
    if args.config:
        try:
            value = json.loads(pathlib.Path(args.config).read_text(encoding="utf-8")).get("api_key")
            if isinstance(value, str) and value:
                return value
        except (OSError, ValueError):
            pass
    return os.environ.get(args.api_key_env, "")


def request_json(url: str, key: str, timeout: float, body: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def token_block(tokens: int, tag: str) -> str:
    """A deterministic block close to the requested token count.

    The server reports the exact prompt size.  One short English word is close
    to one token for this template; that is only used to keep runs comparable,
    never as a reported token count.
    """
    count = max(1, int(tokens))
    words = " ".join(WORDS[index % len(WORDS)] for index in range(count))
    return f"{tag} {words}"


class Conversation:
    def __init__(
        self,
        base_url: str,
        model: str,
        key: str,
        level: int,
        wave: int,
        index: int,
        context_tokens: int,
        prefix_fraction: float,
        max_tokens: int,
        timeout: float,
    ):
        self.base_url = base_url
        self.model = model
        self.key = key
        self.level = level
        self.wave = wave
        self.index = index
        self.max_tokens = max_tokens
        self.timeout = timeout
        tag = f"{RUN_ID}-w{wave:02d}-n{level}-s{index}"
        prefix_tokens = max(0, int(context_tokens * prefix_fraction))
        private_tokens = max(1, context_tokens - prefix_tokens)
        shared = token_block(prefix_tokens, f"{RUN_ID}-shared") if prefix_tokens else ""
        private = token_block(private_tokens, f"{tag}-private")
        self.messages = [
            {
                "role": "system",
                "content": (
                    "You are a coding agent. Inspect the supplied project notes and "
                    "answer each tool-result turn concisely.\n\n" + shared
                ),
            },
            {
                "role": "user",
                "content": (
                    "Project notes for this independent session:\n\n" + private +
                    "\n\nReply with exactly READY."
                ),
            },
        ]
        self.round = 0

    def begin_round(self) -> int:
        self.round += 1
        if self.round > 1:
            self.messages.append(
                {
                    "role": "user",
                    "content": (
                        "Tool result:\n\n" +
                        token_block(
                            384,
                            f"{RUN_ID}-w{self.wave:02d}-n{self.level}-s{self.index}-r{self.round}",
                        ) +
                        "\n\nReply with exactly CONTINUE."
                    ),
                }
            )
        return self.round

    def complete_round(self, reply: str) -> None:
        self.messages.append({"role": "assistant", "content": reply})

    def chat(self) -> dict:
        body = {
            "model": self.model,
            "messages": self.messages,
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "reasoning_effort": "none",
        }
        started = time.monotonic()
        try:
            payload = request_json(
                self.base_url + "/v1/chat/completions",
                self.key,
                self.timeout,
                body,
            )
            error = None
        except Exception as exc:  # noqa: BLE001 - preserve a partial run
            payload = {}
            error = f"{type(exc).__name__}: {exc}"
        wall_s = time.monotonic() - started
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        reply = message.get("content") or ""
        usage = payload.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        metrics = payload.get("metrics") or {}
        timings = payload.get("timings") or {}
        return {
            "level": self.level,
            "wave": self.wave,
            "session": self.index,
            "round": self.round,
            "wall_s": round(wall_s, 6),
            "prompt_tokens": usage.get("prompt_tokens"),
            "cached_tokens": details.get("cached_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "prompt_ms": timings.get("prompt_ms"),
            "decode_ms": timings.get("predicted_ms"),
            "decode_tok_s": timings.get("predicted_per_second"),
            "ttft_ms": metrics.get("ttft_ms"),
            "slot_wait_ms": metrics.get("slot_wait_ms"),
            "itl_ms_mean": metrics.get("itl_ms_mean"),
            "finish": choice.get("finish_reason"),
            "error": error,
            "_reply": reply,
        }


def metrics(base_url: str, key: str) -> dict:
    return request_json(base_url + "/metrics", key, 10)


def run_wave(
    args: argparse.Namespace,
    key: str,
    level: int,
    wave: int,
    rounds: int,
) -> list[dict]:
    conversations = [
        Conversation(
            args.base_url,
            args.model,
            key,
            level,
            wave,
            index,
            args.context_tokens,
            args.shared_prefix_fraction,
            args.max_tokens,
            args.timeout,
        )
        for index in range(level)
    ]
    rows: list[dict] = []
    for round_index in range(1, rounds + 1):
        for conversation in conversations:
            conversation.begin_round()
        barrier = threading.Barrier(level + 1)

        def worker(conversation: Conversation) -> dict:
            barrier.wait()
            return conversation.chat()

        with concurrent.futures.ThreadPoolExecutor(max_workers=level) as pool:
            futures = [pool.submit(worker, conversation) for conversation in conversations]
            barrier.wait()
            wave_rows = [future.result() for future in futures]
        for conversation, row in zip(conversations, wave_rows):
            reply = row.pop("_reply")
            if not row["error"]:
                conversation.complete_round(reply)
            rows.append(row)
        time.sleep(args.settle_s)
    return rows


def numeric(rows: list[dict], key: str) -> list[float]:
    return [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]


def summarize(rows: list[dict]) -> dict:
    output: dict = {}
    for level in sorted({row["level"] for row in rows}):
        output[str(level)] = {}
        for round_index in sorted({row["round"] for row in rows if row["level"] == level}):
            selected = [
                row for row in rows
                if row["level"] == level and row["round"] == round_index and not row["error"]
            ]
            prompt = numeric(selected, "prompt_tokens")
            cached = numeric(selected, "cached_tokens")
            summary = {
                "requests": len(selected),
                "errors": sum(
                    1 for row in rows if row["level"] == level and row["round"] == round_index and row["error"]
                ),
                "prompt_tokens_median": statistics.median(prompt) if prompt else None,
                "reuse_pct_median": (
                    100.0 * statistics.median(
                        [
                            cached_value / prompt_value
                            for prompt_value, cached_value in zip(prompt, cached)
                            if prompt_value > 0
                        ]
                    )
                    if prompt and cached else None
                ),
            }
            for key in ("prompt_ms", "decode_ms", "decode_tok_s", "ttft_ms", "slot_wait_ms", "wall_s"):
                values = numeric(selected, key)
                summary[key + "_median"] = statistics.median(values) if values else None
                summary[key + "_range"] = [min(values), max(values)] if values else None
            output[str(level)][str(round_index)] = summary
    return output


def wave_summary(rows: list[dict]) -> dict:
    """Per-wave medians used later to calculate A/A dispersion."""
    output: dict = {}
    for level in sorted({row["level"] for row in rows}):
        output[str(level)] = {}
        for round_index in sorted({row["round"] for row in rows if row["level"] == level}):
            waves = {}
            for wave in sorted({row["wave"] for row in rows if row["level"] == level}):
                selected = [
                    row for row in rows
                    if row["level"] == level and row["round"] == round_index
                    and row["wave"] == wave and not row["error"]
                ]
                values = numeric(selected, "wall_s")
                waves[str(wave)] = {
                    "requests": len(selected),
                    "wall_s_median": statistics.median(values) if values else None,
                }
            output[str(level)][str(round_index)] = waves
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--config", help="Strata config JSON whose api_key is used when no env key is present")
    parser.add_argument("--api-key-env", default="STRATA_API_KEY")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--levels", default="1,2,4")
    parser.add_argument("--waves", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--context-tokens", type=int, default=8192)
    parser.add_argument("--shared-prefix-fraction", type=float, default=0.25)
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--settle-s", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    levels = [int(value) for value in args.levels.split(",")]
    if not args.waves or not args.rounds:
        parser.error("--waves and --rounds must be positive")
    if any(value < 1 or value > 4 for value in levels):
        parser.error("--levels must be within 1..4 for the agent workload")
    if not 0 <= args.shared_prefix_fraction <= 1:
        parser.error("--shared-prefix-fraction must be within 0..1")

    key = api_key(args)
    if not args.no_warmup:
        for level in levels:
            warm_rows = run_wave(args, key, level, -1, args.rounds)
            failed = [row for row in warm_rows if row["error"]]
            if failed:
                print(json.dumps({"error": "warmup: " + failed[0]["error"]}))
                return 1
    before_metrics = metrics(args.base_url, key)
    before_totals = before_metrics.get("totals") or {}
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows: list[dict] = []
    for level in levels:
        for wave in range(args.waves):
            wave_rows = run_wave(args, key, level, wave, args.rounds)
            rows.extend(wave_rows)
            successful = sum(1 for row in wave_rows if not row["error"])
            print(f"level={level} wave={wave + 1}/{args.waves} requests={successful}/{len(wave_rows)}", flush=True)

    after_metrics = metrics(args.base_url, key)
    after_totals = after_metrics.get("totals") or {}
    expected_requests = sum(levels) * args.waves * args.rounds
    requests_seen = int(after_totals.get("requests") or 0) - int(before_totals.get("requests") or 0)
    output = {
        "run_id": RUN_ID,
        "started": started,
        "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "args": {
            key: value for key, value in vars(args).items()
            if key not in {"api_key_env", "config", "no_warmup"}
        },
        "identity_before": {
            "engine": before_metrics.get("engine"),
            "conversation_cache": before_metrics.get("conversation_cache"),
        },
        "identity_after": {
            "engine": after_metrics.get("engine"),
            "conversation_cache": after_metrics.get("conversation_cache"),
        },
        "request_accounting": {
            "expected": expected_requests,
            "seen": requests_seen,
            "foreign": max(0, requests_seen - expected_requests),
        },
        "summary": summarize(rows),
        "waves": wave_summary(rows),
        "requests": rows,
    }
    path = pathlib.Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(path),
        "requests": len(rows),
        "errors": sum(1 for row in rows if row["error"]),
        "foreign": output["request_accounting"]["foreign"],
    }, sort_keys=True))
    return 0 if not any(row["error"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
