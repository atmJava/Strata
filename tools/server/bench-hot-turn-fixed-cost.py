#!/usr/bin/env python3
"""Benchmark ordinary agent hot continuations without recording prompt text.

One run covers an arm such as short_read=64 or short_read=0.  Each wave starts
one or two independent conversations, then sends:

1. a cold turn with about 12K context tokens;
2. a hot turn with a short fresh tool/result header;
3. a hot turn with about 596 fresh tokens.

The output stores timing, token counts, finish, reply length/hash and an
expected-reply boolean.  It never stores a prompt, reply, reasoning text or API
key.  Compare completed A64/B0/A64r runs with --compare.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import pathlib
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request


DEFAULT_MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"
WORDS = (
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
    "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
    "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform",
    "victor", "whiskey", "xray", "yankee", "zulu", "pipeline", "report",
    "state", "owner", "deadline", "checkpoint", "compile", "pass", "diff",
)
TURN_EXPECT = {
    "cold": "COLD-ACK",
    "hot_short": "HOT-SHORT-ACK",
    "hot_long": "HOT-LONG-ACK",
}


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


def words(count: int, tag: str) -> str:
    count = max(1, int(count))
    values = (WORDS[index % len(WORDS)] for index in range(count))
    return f"{tag} " + " ".join(values)


class Conversation:
    def __init__(
        self,
        base_url: str,
        model: str,
        key: str,
        arm: str,
        level: int,
        wave: int,
        session: int,
        context_tokens: int,
        shared_prefix_fraction: float,
        short_fresh_tokens: int,
        long_fresh_tokens: int,
        max_tokens: int,
        timeout: float,
    ):
        self.base_url = base_url
        self.model = model
        self.key = key
        self.arm = arm
        self.level = level
        self.wave = wave
        self.session = session
        self.short_fresh_tokens = short_fresh_tokens
        self.long_fresh_tokens = long_fresh_tokens
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.turn = ""

        tag = f"{arm}-w{wave:02d}-n{level}-s{session}"
        shared_tokens = int(context_tokens * shared_prefix_fraction)
        private_tokens = max(1, context_tokens - shared_tokens)
        shared = words(shared_tokens, f"shared-{wave:02d}-{arm}") if shared_tokens else ""
        private = words(private_tokens, f"private-{tag}")
        code = "\n\n```javascript\nconst sentinel = 'ok </script>';\n```"
        self.messages = [
            {
                "role": "system",
                "content": (
                    "You are a coding agent. Treat the following project notes as "
                    "history and obey the final exact-reply instruction.\n\n" + shared
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Session notes {tag}:\n\n{private}\n\n"
                    f"Rendering fixture:{code}\n\n"
                    "Reply with exactly COLD-ACK."
                ),
            },
        ]

    def begin_turn(self) -> str:
        if not self.turn:
            self.turn = "cold"
            return self.turn
        if self.turn == "cold":
            self.turn = "hot_short"
            fresh_tokens = self.short_fresh_tokens
            expected = TURN_EXPECT[self.turn]
        elif self.turn == "hot_short":
            self.turn = "hot_long"
            fresh_tokens = self.long_fresh_tokens
            expected = TURN_EXPECT[self.turn]
        else:
            raise RuntimeError("conversation already completed")
        fresh = words(
            fresh_tokens,
            f"tool-result-{self.arm}-w{self.wave:02d}-n{self.level}-s{self.session}",
        )
        self.messages.append(
            {
                "role": "user",
                "content": f"Tool result:\n\n{fresh}\n\nReply with exactly {expected}.",
            }
        )
        return self.turn

    def complete_turn(self, reply: str) -> None:
        self.messages.append({"role": "assistant", "content": reply})

    def chat(self, turn: str) -> dict:
        body = {
            "model": self.model,
            "messages": self.messages,
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "top_p": 1,
            "top_k": 1,
            "reasoning_effort": "none",
        }
        started = time.monotonic()
        status = None
        error = None
        try:
            payload = request_json(
                self.base_url + "/v1/chat/completions",
                self.key,
                self.timeout,
                body,
            )
        except urllib.error.HTTPError as exc:
            payload = {}
            status = exc.code
            error = f"HTTPError: {exc.code}"
        except Exception as exc:  # noqa: BLE001 - retain a failed sample
            payload = {}
            error = type(exc).__name__
        wall_s = time.monotonic() - started

        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        reply = message.get("content") or ""
        usage = payload.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        metrics = payload.get("metrics") or {}
        timings = payload.get("timings") or {}
        expected = TURN_EXPECT[turn]
        return {
            "arm": self.arm,
            "level": self.level,
            "wave": self.wave,
            "session": self.session,
            "turn": turn,
            "status": status,
            "wall_s": round(wall_s, 6),
            "prompt_tokens": usage.get("prompt_tokens"),
            "cached_tokens": details.get("cached_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "prompt_ms": timings.get("prompt_ms"),
            "decode_ms": timings.get("predicted_ms"),
            "ttft_ms": metrics.get("ttft_ms"),
            "slot_wait_ms": metrics.get("slot_wait_ms"),
            "finish": choice.get("finish_reason"),
            "reply_chars": len(reply),
            "reply_sha256": hashlib.sha256(reply.encode()).hexdigest(),
            "reply_matches_expected": reply.strip() == expected,
            "error": error,
            "_reply": reply,
        }


def run_wave(args: argparse.Namespace, key: str, level: int, wave: int) -> list[dict]:
    conversations = [
        Conversation(
            args.base_url,
            args.model,
            key,
            args.arm,
            level,
            wave,
            session,
            args.context_tokens,
            args.shared_prefix_fraction,
            args.short_fresh_tokens,
            args.long_fresh_tokens,
            args.max_tokens,
            args.timeout,
        )
        for session in range(level)
    ]
    rows: list[dict] = []
    for _ in range(3):
        turns = [conversation.begin_turn() for conversation in conversations]
        if len(set(turns)) != 1:
            raise RuntimeError("conversation turns are not aligned")
        barrier = threading.Barrier(level + 1)

        def worker(conversation: Conversation, turn: str) -> dict:
            barrier.wait()
            return conversation.chat(turn)

        with concurrent.futures.ThreadPoolExecutor(max_workers=level) as pool:
            futures = [pool.submit(worker, conversation, turns[i]) for i, conversation in enumerate(conversations)]
            barrier.wait()
            wave_rows = [future.result() for future in futures]
        for conversation, row in zip(conversations, wave_rows):
            reply = row.pop("_reply")
            if not row["error"]:
                conversation.complete_turn(reply)
            rows.append(row)
        time.sleep(args.settle_s)
    return rows


def median(rows: list[dict], field: str) -> float | None:
    values = [
        float(row[field]) for row in rows
        if not row["error"] and isinstance(row.get(field), (int, float))
    ]
    return statistics.median(values) if values else None


def summarize(rows: list[dict]) -> dict:
    output: dict = {}
    for level in sorted({row["level"] for row in rows}):
        output[str(level)] = {}
        for turn in ("cold", "hot_short", "hot_long"):
            selected = [
                row for row in rows
                if row["level"] == level and row["turn"] == turn
            ]
            successful = [row for row in selected if not row["error"]]
            output[str(level)][turn] = {
                "requests": len(selected),
                "errors": len(selected) - len(successful),
                "prompt_tokens_median": median(successful, "prompt_tokens"),
                "cached_tokens_median": median(successful, "cached_tokens"),
                "prompt_ms_median": median(successful, "prompt_ms"),
                "wall_s_median": median(successful, "wall_s"),
                "decode_ms_median": median(successful, "decode_ms"),
                "ttft_ms_median": median(successful, "ttft_ms"),
                "slot_wait_ms_median": median(successful, "slot_wait_ms"),
                "finish": sorted({str(row["finish"]) for row in successful}),
                "replies_expected": sum(bool(row["reply_matches_expected"]) for row in successful),
            }
    return output


def run_arm(args: argparse.Namespace) -> int:
    key = api_key(args)
    before = request_json(args.base_url + "/metrics", key, 15)
    before_totals = before.get("totals") or {}
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rows: list[dict] = []
    for level in args.levels:
        for wave in range(args.waves):
            wave_rows = run_wave(args, key, level, wave)
            rows.extend(wave_rows)
            ok = sum(not row["error"] for row in wave_rows)
            print(f"arm={args.arm} level={level} wave={wave + 1}/{args.waves} requests={ok}/{len(wave_rows)}", flush=True)

    after = request_json(args.base_url + "/metrics", key, 15)
    after_totals = after.get("totals") or {}
    expected_requests = sum(args.levels) * args.waves * 3
    requests_seen = int(after_totals.get("requests") or 0) - int(before_totals.get("requests") or 0)
    result = {
        "run_id": run_id,
        "arm": args.arm,
        "args": {
            name: value for name, value in vars(args).items()
            if name not in {"api_key_env", "config"}
        },
        "identity_before": {
            "engine": before.get("engine"),
            "conversation_cache": before.get("conversation_cache"),
        },
        "identity_after": {
            "engine": after.get("engine"),
            "conversation_cache": after.get("conversation_cache"),
        },
        "request_accounting": {
            "expected": expected_requests,
            "seen": requests_seen,
            "foreign": max(0, requests_seen - expected_requests),
        },
        "summary": summarize(rows),
        "requests": rows,
    }
    path = pathlib.Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    problems = [row for row in rows if row["error"] or not row["reply_matches_expected"]]
    print(json.dumps({
        "output": str(path),
        "requests": len(rows),
        "problems": len(problems),
        "foreign": result["request_accounting"]["foreign"],
    }, sort_keys=True))
    return 1 if problems or result["request_accounting"]["foreign"] else 0


def wave_medians(rows: list[dict], level: int, turn: str, field: str) -> dict[int, float]:
    output: dict[int, float] = {}
    for wave in sorted({row["wave"] for row in rows}):
        selected = [
            row for row in rows
            if row["level"] == level and row["turn"] == turn and row["wave"] == wave
        ]
        value = median(selected, field)
        if value is not None:
            output[wave] = value
    return output


def compare(paths: list[pathlib.Path]) -> tuple[dict, list[str]]:
    runs = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    by_arm = {run["arm"]: run for run in runs}
    required = {"A64", "B0", "A64r"}
    problems: list[str] = []
    if set(by_arm) != required:
        return {}, [f"arms must be exactly {sorted(required)}, got {sorted(by_arm)}"]
    for arm, run in by_arm.items():
        if run["request_accounting"]["foreign"]:
            problems.append(f"{arm}: {run['request_accounting']['foreign']} foreign requests")
        for level, turns in run["summary"].items():
            for turn, summary in turns.items():
                if summary["errors"]:
                    problems.append(f"{arm} level {level} {turn}: {summary['errors']} errors")
                if summary["finish"] != ["stop"]:
                    problems.append(f"{arm} level {level} {turn}: finish {summary['finish']}")
                if summary["replies_expected"] != summary["requests"]:
                    problems.append(
                        f"{arm} level {level} {turn}: "
                        f"{summary['replies_expected']}/{summary['requests']} expected replies"
                    )

    comparison: dict = {}
    levels = sorted({
        int(level)
        for run in runs
        for level in run["summary"]
    })
    for level in levels:
        level_key = str(level)
        comparison[level_key] = {}
        for turn in ("cold", "hot_short", "hot_long"):
            comparison[level_key][turn] = {}
            for field in ("prompt_ms", "wall_s"):
                arm_waves = {
                    arm: wave_medians(by_arm[arm]["requests"], level, turn, field)
                    for arm in required
                }
                common = sorted(set.intersection(*(set(values) for values in arm_waves.values())))
                if not common:
                    problems.append(f"level {level} {turn} {field}: no complete waves")
                    continue
                medians = {
                    arm: statistics.median(arm_waves[arm][wave] for wave in common)
                    for arm in required
                }
                drift = (
                    abs(medians["A64r"] - medians["A64"]) / medians["A64"] * 100
                    if medians["A64"] else float("inf")
                )
                reference_waves = {
                    wave: statistics.median([arm_waves["A64"][wave], arm_waves["A64r"][wave]])
                    for wave in common
                }
                reference = statistics.median(reference_waves.values())
                change = (
                    (reference - medians["B0"]) / reference * 100
                    if reference else float("-inf")
                )
                wins = sum(medians["B0"] < reference_waves[wave] for wave in common)
                comparison[level_key][turn][field] = {
                    "A64": medians["A64"],
                    "B0": medians["B0"],
                    "A64r": medians["A64r"],
                    "A64r_drift_pct": drift,
                    "paired_change_pct": change,
                    "B0_wins": wins,
                    "waves": len(common),
                }
                if drift > 3:
                    problems.append(
                        f"level {level} {turn} {field}: A64r drift {drift:.3f}%"
                    )

    hot_rows = [
        (int(level), turn, field, result)
        for level, turns in comparison.items()
        for turn in ("hot_short", "hot_long")
        for field, result in turns[turn].items()
    ]
    gains = [
        row for row in hot_rows
        if row[0] in (1, 2) and row[3]["paired_change_pct"] >= 1 and row[3]["B0_wins"] >= 2
    ]
    absolute_savings = [
        abs(row[3]["A64"] - row[3]["B0"])
        for row in gains
        if row[2] == "wall_s"
    ]
    if not gains:
        problems.append("no ordinary hot-turn class reached a 1% median gain with 2/3 waves")
    elif not absolute_savings or max(absolute_savings) < 0.15:
        problems.append("no passing wall class saved at least 0.15 s")
    for level, turn, field, result in hot_rows:
        if result["paired_change_pct"] < -1:
            problems.append(
                f"level {level} {turn} {field}: regressed {result['paired_change_pct']:.3f}%"
            )
    verdict = {
        "pass": not problems,
        "problems": problems,
        "comparison": comparison,
    }
    return verdict, problems


def parse_levels(value: str) -> list[int]:
    levels = [int(item) for item in value.split(",")]
    if any(level not in (1, 2, 4) for level in levels):
        raise argparse.ArgumentTypeError("levels must use 1, 2 and/or 4")
    return levels


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--config", help="Strata config JSON whose api_key is used when no env key is present")
    parser.add_argument("--api-key-env", default="STRATA_API_KEY")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--arm")
    parser.add_argument("--levels", type=parse_levels, default=[1, 2])
    parser.add_argument("--waves", type=int, default=3)
    parser.add_argument("--context-tokens", type=int, default=12000)
    parser.add_argument("--shared-prefix-fraction", type=float, default=0.25)
    parser.add_argument("--short-fresh-tokens", type=int, default=24)
    parser.add_argument("--long-fresh-tokens", type=int, default=596)
    parser.add_argument("--max-tokens", type=int, default=8)
    parser.add_argument("--settle-s", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--output")
    parser.add_argument("--compare", nargs=3, metavar="JSON")
    args = parser.parse_args()

    if args.compare:
        verdict, problems = compare([pathlib.Path(path) for path in args.compare])
        print(json.dumps(verdict, ensure_ascii=False, indent=1, sort_keys=True))
        return 0 if not problems else 1
    if not args.arm or not args.output:
        parser.error("run mode needs --arm and --output")
    if args.waves < 3:
        parser.error("--waves must be at least 3")
    if not 0 <= args.shared_prefix_fraction <= 1:
        parser.error("--shared-prefix-fraction must be within 0..1")
    return run_arm(args)


if __name__ == "__main__":
    sys.exit(main())
