#!/usr/bin/env python3
"""Measure the host-side preparation cost of a DSH-shaped Responses request.

The probe uses the production tokenizer and chat template, but no engine and no
live server. It reports aggregate timings and token counts only: request bodies,
message text, generated text and credentials are never written.

Run it on the production host with the pack's tokenizer directory, for example:

  ./tools/server/probe-host-request-path.py \
      --tokenizer /opt/ai/strata-data/packs/sc117_iq3_xxs/tokenizer \
      --template /opt/ai/strata-data/packs/sc117_iq3_xxs/tokenizer/chat_template.jinja \
      --contexts 6000,94700,189100 --reps 7 --output /tmp/host-path.json
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import pathlib
import statistics
import sys
import threading
import time
from typing import Any, Callable


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

from serve import responses as responses_api  # noqa: E402
from serve.frontend import (ChatTemplate, mark_think_literals,  # noqa: E402
                            unmark_think_literals)
from serve.server import DSH_MAIN_TOOLS, MockEngine, Service  # noqa: E402
from serve.structured import prepare_format  # noqa: E402


WORDS = (
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
    "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
    "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform",
    "victor", "whiskey", "xray", "yankee", "zulu", "project", "source",
    "test", "change", "review", "state", "request", "prompt", "cache",
)


def percentile(values: list[float], fraction: float) -> float | None:
    """Linear percentile, or None for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    at = (len(ordered) - 1) * min(1.0, max(0.0, fraction))
    lo = math.floor(at)
    hi = math.ceil(at)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (at - lo)


def timing_summary_ms(values: list[float]) -> dict[str, Any]:
    """Median, p90 and spread for values already expressed in milliseconds."""
    rounded = [round(value, 3) for value in values]
    median = percentile(values, 0.5)
    p90 = percentile(values, 0.9)
    return {
        "n": len(values),
        "median_ms": round(median, 3) if median is not None else None,
        "p90_ms": round(p90, 3) if p90 is not None else None,
        "min_ms": round(min(values), 3) if values else None,
        "max_ms": round(max(values), 3) if values else None,
        "samples_ms": rounded,
    }


def timed_ms(call: Callable[[], Any]) -> tuple[float, Any]:
    started = time.perf_counter_ns()
    result = call()
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return elapsed_ms, result


def load_tokenizer(path: pathlib.Path):
    """Load exactly the tokenizer files the server loads for a pack."""
    import strata_tokenizer as tokenizer_module

    vocab = json.loads((path / "vocab.json").read_text(encoding="utf-8"))
    tokens = [None] * len(vocab)
    for token, index in vocab.items():
        tokens[index] = token
    merges = (path / "merges.txt").read_text(encoding="utf-8").split("\n")
    token_types = json.loads((path / "token_type.json").read_text(encoding="utf-8"))
    config = {}
    try:
        config = json.loads((path / "tokenizer.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return tokenizer_module.Tokenizer(
        tokens,
        merges,
        token_types,
        config.get("pre") or "qwen35",
        config.get("special_ids") or None,
    )


def dsh_tools() -> list[dict[str, Any]]:
    """The current DSH tool surface with deterministic representative schemas."""
    tools = []
    for index, name in enumerate(DSH_MAIN_TOOLS):
        description = (
            f"{name} tool for a coding agent. "
            + " ".join(WORDS[(index + offset) % len(WORDS)] for offset in range(72))
        )
        tools.append({
            "type": "function",
            "name": name,
            "description": description,
            "strict": False,
            "parameters": {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": "The task-specific input for this tool.",
                    }
                },
                "required": ["input"],
            },
        })
    return tools


def filler_words(count: int, tag: str) -> str:
    """Deterministic prose without message-body output or external assets."""
    values = (WORDS[index % len(WORDS)] for index in range(max(0, count)))
    return f"{tag} " + " ".join(values)


def make_items(extra_words: int) -> list[dict[str, Any]]:
    system = (
        "You are a coding agent working in a local repository. Follow the user's "
        "request, inspect before editing, preserve unrelated changes and verify the "
        "result with focused tests. Return tool calls in the declared protocol.\n\n"
        + filler_words(1250, "system-context")
    )
    return [
        {"role": "developer", "content": system},
        {"role": "user", "content": "Current directory: /tmp/dsh-project"},
        {"role": "user", "content": filler_words(max(1, extra_words), "history-context")},
        {"role": "user", "content": "Inspect the relevant files, make the requested change and run the focused test."},
        {"role": "user", "content": "Use the available tools and verify your work."},
    ]


def encode_count(service: Service, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                 kwargs: dict[str, Any]) -> int:
    return len(service.encode_prompt(messages, tools, kwargs, store=False))


def build_request(service: Service, target_tokens: int, tolerance: int) -> tuple[dict[str, Any], int]:
    """Choose a filler size whose rendered prompt is close to target_tokens."""
    tools = dsh_tools()
    kwargs = responses_api.template_kwargs({}, {"reasoning_effort": service.effort_default()})
    kwargs["system_before_tools"] = service.codex_system_before_tools

    low, high = 1, max(32, target_tokens * 2)
    best: tuple[int, dict[str, Any], int] | None = None
    for _ in range(14):
        middle = (low + high) // 2
        req = {
            "model": "probe",
            "stream": True,
            "max_output_tokens": 32000,
            "tools": tools,
            "input": make_items(middle),
        }
        messages = responses_api.input_messages(req)
        count = encode_count(service, messages, tools, kwargs)
        distance = abs(count - target_tokens)
        if best is None or distance < best[0]:
            best = (distance, req, count)
        if distance <= tolerance:
            break
        if count < target_tokens:
            low = middle + 1
        else:
            high = middle - 1
    if best is None:
        raise RuntimeError("could not build the DSH-shaped request")
    return best[1], best[2]


def responses_prepare(service: Service, req: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None,
                                                                      dict[str, Any]]:
    responses_api.check_request(req)
    messages = responses_api.input_messages(req)
    tools, _, _ = responses_api.request_tools(req)
    kwargs = responses_api.template_kwargs(req, {"reasoning_effort": service.effort_default()})
    kwargs["system_before_tools"] = service.codex_system_before_tools
    messages, _ = prepare_format(responses_api.text_format(req), messages, with_tools=bool(tools))
    return messages, tools, kwargs


def render_and_encode(service: Service, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                      kwargs: dict[str, Any]) -> tuple[float, float, int, int]:
    """Measure render and PromptEncoder separately against the same prepared request."""
    render_ms, rendered = timed_ms(lambda: service.render_prompt(messages, tools, kwargs))
    marked, marked_tools, changed = mark_think_literals(messages, tools, service.literals)
    marked_prompt = service.render_prompt(marked, marked_tools, kwargs)
    plain: tuple = ()
    if changed:
        marked_prompt, plain = unmark_think_literals(marked_prompt, service.literals)
    encode_ms, ids = timed_ms(lambda: service.prompts.encode(marked_prompt, plain, store=False))
    return render_ms, encode_ms, len(ids), service.prompts.last_reused


def append_hot_turn(items: list[dict[str, Any]], index: int) -> None:
    """Append one short assistant/tool round trip like a DSH editing step."""
    call_id = f"call-{index}"
    items.extend([
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Inspecting the focused files."}],
        },
        {
            "type": "function_call",
            "call_id": call_id,
            "name": "bash",
            "arguments": json.dumps({
                "description": "Run focused test",
                "command": "python -m pytest -q",
                "workdir": "/tmp/dsh-project",
            }, separators=(",", ":")),
        },
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": "focused test passed",
        },
    ])


def measure_context(tokenizer, template: ChatTemplate, target_tokens: int, reps: int,
                    tolerance: int) -> dict[str, Any]:
    """Measure one synthetic DSH context with cold preparation and hot continuations."""
    engine = MockEngine(tokenizer, "", max_context=262144)
    service = Service(engine, tokenizer, template, model_name="probe")
    req, built_tokens = build_request(service, target_tokens, tolerance)
    items = json.loads(json.dumps(req["input"]))
    req = {**req, "input": items}
    body = json.dumps(req, ensure_ascii=False, separators=(",", ":")).encode()

    parse_ms = []
    for _ in range(reps):
        elapsed, parsed = timed_ms(lambda: json.loads(body))
        if not isinstance(parsed, dict):
            raise RuntimeError("request did not decode to an object")
        parse_ms.append(elapsed)

    conversion_ms = []
    messages = tools = kwargs = None
    for _ in range(reps):
        parsed = json.loads(body)
        elapsed, prepared = timed_ms(lambda: responses_prepare(service, parsed))
        conversion_ms.append(elapsed)
        messages, tools, kwargs = prepared
    if messages is None or tools is None or kwargs is None:
        raise RuntimeError("Responses preparation returned no request")

    render_ms, encode_ms, rendered_tokens, reused_chars = render_and_encode(
        service, messages, tools, kwargs
    )

    # Exercise the production DSH pin and the complete Service.prepare path.
    service.accept_dsh_tool_prefix(messages, tools, kwargs)
    pinned_req = {**json.loads(body), "strata_prefix": {"messages": 1}}
    cold_started = time.perf_counter_ns()
    ids, _, _ = service.prepare(
        messages,
        tools,
        kwargs,
        max_new=32000,
        req=pinned_req,
        cancel=threading.Event(),
    )
    cold_prepare_ms = (time.perf_counter_ns() - cold_started) / 1_000_000

    cold_names = responses_api.request_tools(pinned_req)[1]
    assembler_started = time.perf_counter_ns()
    responses_api.Assembler(pinned_req, service.model_for(pinned_req), len(ids), cold_names, False, False)
    assembler_ms = (time.perf_counter_ns() - assembler_started) / 1_000_000

    hot_prepare_ms = []
    hot_parse_ms = []
    hot_conversion_ms = []
    hot_tokens = []
    hot_reused_chars = []
    for index in range(reps):
        append_hot_turn(items, index)
        hot_req = {**req, "input": items, "strata_prefix": {"messages": 1}}
        hot_body = json.dumps(hot_req, ensure_ascii=False, separators=(",", ":")).encode()
        parse_elapsed, parsed = timed_ms(lambda: json.loads(hot_body))
        hot_parse_ms.append(parse_elapsed)

        conversion_elapsed, prepared = timed_ms(lambda: responses_prepare(service, parsed))
        hot_conversion_ms.append(conversion_elapsed)
        hot_messages, hot_tools, hot_kwargs = prepared
        if hot_tools is None:
            raise RuntimeError("hot request has no tools")

        prepare_started = time.perf_counter_ns()
        hot_ids, _, _ = service.prepare(
            hot_messages,
            hot_tools,
            hot_kwargs,
            max_new=32000,
            req=parsed,
            cancel=threading.Event(),
        )
        hot_prepare_ms.append((time.perf_counter_ns() - prepare_started) / 1_000_000)
        hot_tokens.append(len(hot_ids))
        hot_reused_chars.append(service.prompts.last_reused)

    return {
        "target_tokens": target_tokens,
        "built_prompt_tokens": built_tokens,
        "rendered_prompt_tokens": rendered_tokens,
        "cold_prompt_tokens": len(ids),
        "hot_prompt_tokens": hot_tokens,
        "request_bytes": len(body),
        "request_sha256": hashlib.sha256(body).hexdigest(),
        "cold": {
            "json_parse": timing_summary_ms(parse_ms),
            "responses_prepare": timing_summary_ms(conversion_ms),
            "template_render_ms": round(render_ms, 3),
            "prompt_encoder_ms": round(encode_ms, 3),
            "prompt_encoder_reused_chars": reused_chars,
            "service_prepare_ms": round(cold_prepare_ms, 3),
            "assembler_ms": round(assembler_ms, 3),
        },
        "hot": {
            "json_parse": timing_summary_ms(hot_parse_ms),
            "responses_prepare": timing_summary_ms(hot_conversion_ms),
            "service_prepare": timing_summary_ms(hot_prepare_ms),
            "prompt_encoder_reused_chars": hot_reused_chars,
        },
    }


def parse_contexts(value: str) -> list[int]:
    sizes = [int(part) for part in value.split(",") if part.strip()]
    if not sizes or any(size < 1000 for size in sizes):
        raise argparse.ArgumentTypeError("contexts must be comma-separated whole numbers >= 1000")
    return sizes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer", required=True, type=pathlib.Path,
                        help="production tokenizer directory containing vocab.json")
    parser.add_argument("--template", type=pathlib.Path,
                        help="chat template (default: tokenizer directory's chat_template.jinja)")
    parser.add_argument("--contexts", type=parse_contexts, default=[6000, 94700, 189100],
                        help="comma-separated approximate prompt token counts")
    parser.add_argument("--reps", type=int, default=7, help="hot repetitions per context")
    parser.add_argument("--tolerance", type=int, default=256, help="prompt-size tolerance in tokens")
    parser.add_argument("--output", type=pathlib.Path, help="write aggregate JSON here")
    args = parser.parse_args()
    if args.reps < 3:
        parser.error("--reps must be at least 3")
    if args.tolerance < 1:
        parser.error("--tolerance must be positive")
    template_path = args.template or (args.tokenizer / "chat_template.jinja")
    if not (args.tokenizer / "vocab.json").is_file():
        parser.error(f"tokenizer is missing {args.tokenizer / 'vocab.json'}")
    if not template_path.is_file():
        parser.error(f"template is missing {template_path}")

    tokenizer = load_tokenizer(args.tokenizer)
    template = ChatTemplate(template_path)
    gc.collect()
    gc.disable()
    try:
        contexts = [
            measure_context(tokenizer, template, size, args.reps, args.tolerance)
            for size in args.contexts
        ]
    finally:
        gc.enable()
    result = {
        "schema": 1,
        "scope": "host-only DSH request preparation; no prompt, output, tool body or credential is stored",
        "tokenizer": str(args.tokenizer),
        "template": str(template_path),
        "reps": args.reps,
        "tolerance_tokens": args.tolerance,
        "contexts": contexts,
    }
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
