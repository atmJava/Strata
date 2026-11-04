#!/usr/bin/env python3
"""Probe a short prompt while another request is decoding.

The ordinary hot-turn benchmark starts each concurrency level together, so its
prompt phases can finish before a decode window overlaps them.  This probe
warms a deterministic conversation, starts one long decode, then joins with a
short continuation.  It stores timings and hashes only, never prompt or reply
text.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import pathlib
import statistics
import time
import urllib.request


DEFAULT_MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"
WORDS = tuple(
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
    "mike november oscar papa quebec romeo sierra tango uniform victor whiskey "
    "xray yankee zulu pipeline report state owner deadline checkpoint compile "
    "pass diff".split()
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


def request_json(url: str, key: str, body: dict | None = None, timeout: float = 900) -> dict:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def words(count: int, tag: str) -> str:
    return f"{tag} " + " ".join(WORDS[index % len(WORDS)] for index in range(max(1, count)))


def chat(
    base_url: str,
    key: str,
    model: str,
    messages: list[dict],
    max_tokens: int,
    expected: str,
    label: str,
) -> dict:
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0,
        "top_p": 1,
        "top_k": 1,
        "reasoning_effort": "none",
    }
    started = time.monotonic()
    error = None
    try:
        payload = request_json(base_url.rstrip("/") + "/v1/chat/completions", key, body)
    except Exception as exc:  # noqa: BLE001 - retain a failed sample
        payload = {}
        error = f"{type(exc).__name__}: {exc}"
    finished = time.monotonic()
    choice = (payload.get("choices") or [{}])[0]
    reply = (choice.get("message") or {}).get("content") or ""
    usage = payload.get("usage") or {}
    timings = payload.get("timings") or {}
    metrics = payload.get("metrics") or {}
    return {
        "label": label,
        "started_monotonic": started,
        "finished_monotonic": finished,
        "wall_s": round(finished - started, 6),
        "status": None if error is None else "error",
        "error": error,
        "prompt_tokens": usage.get("prompt_tokens"),
        "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
        "prompt_ms": timings.get("prompt_ms"),
        "decode_ms": timings.get("predicted_ms"),
        "ttft_ms": metrics.get("ttft_ms"),
        "finish": choice.get("finish_reason"),
        "reply_matches_expected": reply.strip() == expected,
        "reply_sha256": hashlib.sha256(reply.encode()).hexdigest(),
    }


def summarize(rows: list[dict]) -> dict:
    output: dict = {}
    for label in ("decode", "prompt"):
        selected = [row for row in rows if row["label"] == label and not row["error"]]
        output[label] = {
            "requests": len([row for row in rows if row["label"] == label]),
            "errors": len([row for row in rows if row["label"] == label]) - len(selected),
            "wall_s_median": statistics.median(row["wall_s"] for row in selected) if selected else None,
            "prompt_ms_median": statistics.median(
                row["prompt_ms"] for row in selected if isinstance(row.get("prompt_ms"), (int, float))
            ) if selected else None,
            "decode_ms_median": statistics.median(
                row["decode_ms"] for row in selected if isinstance(row.get("decode_ms"), (int, float))
            ) if selected else None,
        }
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--config", help="Strata config JSON whose api_key is used when no env key is present")
    parser.add_argument("--api-key-env", default="STRATA_API_KEY")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--waves", type=int, default=3)
    parser.add_argument("--context-tokens", type=int, default=12000)
    parser.add_argument("--trigger-fresh-tokens", type=int, default=596)
    parser.add_argument("--decode-tokens", type=int, default=256)
    parser.add_argument("--decode-requests", type=int, default=3)
    parser.add_argument("--lead-s", type=float, default=1.5)
    parser.add_argument("--settle-s", type=float, default=0.5)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if (
        args.waves < 1
        or args.decode_requests < 1
        or min(args.context_tokens, args.trigger_fresh_tokens, args.decode_tokens) < 1
    ):
        parser.error("waves and token counts must be positive")
    if args.lead_s < 0:
        parser.error("--lead-s must not be negative")

    key = api_key(args)
    health_before = request_json(args.base_url.rstrip("/") + "/health", key, timeout=15)
    warm_messages = [
        {"role": "system", "content": "You are a deterministic coding agent."},
        {
            "role": "user",
            "content": words(args.context_tokens, "overlap-context") + "\n\nReply exactly WARM-ACK.",
        },
    ]
    warm = chat(args.base_url, key, args.model, warm_messages, 8, "WARM-ACK", "warmup")
    if warm["error"] or not warm["reply_matches_expected"]:
        print(json.dumps({"error": "warmup failed", "warmup": warm}, sort_keys=True))
        return 1
    assistant = {"role": "assistant", "content": "WARM-ACK"}
    decode_messages = warm_messages + [
        assistant,
        {
            "role": "user",
            "content": (
                "Produce a long numbered checklist without a summary. "
                "Continue until the output token limit."
            ),
        },
    ]
    prompt_messages = warm_messages + [
        assistant,
        {
            "role": "user",
            "content": (
                "Tool result:\n\n"
                + words(args.trigger_fresh_tokens, "overlap-tool-result")
                + "\n\nReply exactly OVERLAP-ACK."
            ),
        },
    ]

    waves: list[dict] = []
    for wave in range(args.waves):
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.decode_requests + 1) as pool:
            decode_futures = [
                pool.submit(
                    chat, args.base_url, key, args.model, decode_messages, args.decode_tokens, "", "decode"
                )
                for _ in range(args.decode_requests)
            ]
            time.sleep(args.lead_s)
            prompt_future = pool.submit(
                chat, args.base_url, key, args.model, prompt_messages, 8, "OVERLAP-ACK", "prompt"
            )
            decodes = [future.result() for future in decode_futures]
            prompt = prompt_future.result()
        overlap = (
            any(prompt["started_monotonic"] < decode["finished_monotonic"] for decode in decodes)
            and any(decode["started_monotonic"] < prompt["finished_monotonic"] for decode in decodes)
        )
        waves.append({"wave": wave, "overlap": overlap, "requests": decodes + [prompt]})
        print(
            f"wave={wave + 1}/{args.waves} overlap={overlap} "
            f"errors={sum(bool(decode['error']) for decode in decodes) + int(bool(prompt['error']))}",
            flush=True,
        )
        time.sleep(args.settle_s)

    health_after = request_json(args.base_url.rstrip("/") + "/health", key, timeout=15)
    rows = [row for wave in waves for row in wave["requests"]]
    problems = []
    if health_before.get("loaded") is not True or health_after.get("loaded") is not True:
        problems.append("health was not loaded")
    if not all(wave["overlap"] for wave in waves):
        problems.append("at least one wave did not overlap decode and prompt")
    for row in rows:
        if row["error"]:
            problems.append(f"{row['label']}: {row['error']}")
    for row in rows:
        if row["label"] == "prompt" and not row["reply_matches_expected"]:
            problems.append("prompt reply did not match the sentinel")
    output = {
        "scope": "one long decode plus one short prompt; metadata and hashes only",
        "args": vars(args) | {"api_key_env": None, "config": None},
        "health_before": health_before,
        "health_after": health_after,
        "summary": summarize(rows),
        "waves": waves,
        "problems": problems,
        "pass": not problems,
    }
    path = pathlib.Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(path), "pass": not problems, "problems": problems}, sort_keys=True))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
