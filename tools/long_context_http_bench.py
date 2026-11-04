#!/usr/bin/env python3
"""Measure the production HTTP path at long-context sizes.

The workload is deliberately split into four requests on one loaded engine:

* a cold long document;
* the same document plus a new suffix, which exercises prefix reuse;
* a second cold document with a different prefix;
* that document with an edit near 25% of its length, which exercises the
  conversation checkpoint and partial-reread path.

Each request asks for one output token.  The engine's timing object separates
prompt-read time from decode, while the client records wall TTFT.  This is a
targeted load test, not a replacement for the standard benchmark protocol.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
import urllib.error
import urllib.request


def make_words(words: int, seed: int) -> str:
    """Build deterministic prose-like text with enough vocabulary to avoid pathological tokenization."""
    rng = random.Random(seed)
    vocab = (
        "river stone market lantern copper winter garden signal harbor needle orbit velvet canyon ember "
        "ledger quartz meadow anchor violet thunder archive bridge cipher delta fabric glacier helmet "
        "island journal kernel lattice mirror nectar object packet quiver ribbon shelter timber vector"
    ).split()
    out = []
    for i in range(words):
        out.append(rng.choice(vocab))
        if i % 64 == 63:
            out.append(f"block{i // 64:05d}")
    return " ".join(out)


def chat(base: str, key: str, content: str, timeout: float) -> dict:
    body = {
        "model": "strata",
        "stream": True,
        "max_tokens": 1,
        "temperature": 0,
        "messages": [{"role": "user", "content": content}],
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(
        base + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers=headers,
    )
    t0 = time.perf_counter()
    first = None
    first_text = None
    usage = None
    timings = None
    error = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    item = json.loads(payload)
                except ValueError:
                    continue
                if item.get("error"):
                    error = str(item["error"])[:400]
                for choice in item.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content") or delta.get("reasoning_content"):
                        if first_text is None:
                            first_text = (delta.get("content") or delta.get("reasoning_content") or "")[:120]
                        if first is None:
                            first = time.perf_counter() - t0
                usage = item.get("usage") or usage
                timings = item.get("timings") or timings
    except urllib.error.HTTPError as exc:
        error = f"HTTP {exc.code}: {exc.read()[:400]!r}"
    except Exception as exc:  # noqa: BLE001 - the CLI must report a failed request and continue its matrix
        error = repr(exc)[:400]
    wall_s = time.perf_counter() - t0
    u = usage or {}
    t = timings or {}
    read_n = t.get("prompt_n")
    cached = t.get("cache_n")
    if cached is None:
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens")
    # Strata reports engine prompt_n as the rows it actually read and cache_n
    # as the prefix it mounted.  usage.prompt_tokens is their total.  Keep both
    # rates: read-per-second measures new compute, while effective-per-second
    # measures the end-to-end value of prefix reuse and cannot be read as raw
    # prefill throughput.
    prompt_total = u.get("prompt_tokens")
    if prompt_total is None and read_n is not None:
        prompt_total = read_n + (cached or 0)
    prompt_ms = t.get("prompt_ms")
    read_tps = t.get("prompt_per_second")
    if read_tps is None and read_n is not None and prompt_ms:
        read_tps = read_n / (prompt_ms / 1000.0)
    effective_tps = None
    if prompt_total is not None and prompt_ms:
        effective_tps = prompt_total / (prompt_ms / 1000.0)
    return {
        "ok": error is None and first is not None,
        "error": error,
        "ttft_s": None if first is None else round(first, 3),
        # Keep wall time separate from prompt-token count.  Reusing one variable
        # here silently put token counts in the total_s field of older reports.
        "total_s": round(wall_s, 3),
        "prompt_tokens": prompt_total,
        "cached_tokens": cached,
        "read_tokens": read_n,
        "prompt_ms": prompt_ms,
        "prompt_tps": None if read_tps is None else round(read_tps, 1),
        "effective_prompt_tps": None if effective_tps is None else round(effective_tps, 1),
        "decode_ms": t.get("predicted_ms"),
        "first_text": first_text,
    }


def calibrate(base: str, key: str, timeout: float) -> tuple[float, float]:
    """Return tokens-per-word and fixed overhead from two small requests."""
    points = []
    for words in (500, 2000):
        result = chat(base, key, "Calibration marker.\n" + make_words(words, 991), timeout)
        if not result["ok"] or result["prompt_tokens"] is None:
            raise RuntimeError(f"calibration failed: {result}")
        points.append((words, result["prompt_tokens"]))
    slope = (points[1][1] - points[0][1]) / (points[1][0] - points[0][0])
    overhead = max(0.0, points[0][1] - points[0][0] * slope)
    return slope, overhead


def report(label: str, result: dict, raw: list[dict]) -> None:
    raw.append({"scenario": label, **result})
    if not result["ok"]:
        print(f"{label:20s} FAILED {result['error']}", flush=True)
        return
    print(
        f"{label:20s} prompt={result['prompt_tokens']} cached={result['cached_tokens']} "
        f"read={result['read_tokens']} prompt_ms={result['prompt_ms']} "
        f"read_tps={result['prompt_tps']} effective_tps={result['effective_prompt_tps']} "
        f"ttft={result['ttft_s']}s",
        flush=True,
    )


def read_key_file(path: str) -> str:
    """Read a server key from a Strata config without putting it in argv or logs.

    The server accepts either a top-level string or a small mapping of names to
    keys.  Keep the selection deliberately narrow so a malformed or expanded
    config fails visibly instead of accidentally sending an unrelated secret.
    """
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    for field in ("api_key", "apiKey", "key"):
        value = config.get(field)
        if isinstance(value, str) and value:
            return value
    keys = config.get("api_keys")
    if isinstance(keys, dict):
        active = config.get("api_key_name") or config.get("apiKeyName")
        if isinstance(active, str) and isinstance(keys.get(active), str) and keys[active]:
            return keys[active]
        values = [value for value in keys.values() if isinstance(value, str) and value]
        if len(values) == 1:
            return values[0]
    raise ValueError(f"{path}: no unambiguous API key")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--key", default="")
    parser.add_argument("--key-file", help="read api_key/apiKey/key from a JSON config instead of passing it in argv")
    parser.add_argument("--tokens", type=int, nargs="+", default=[150000, 210000])
    parser.add_argument(
        "--seed-base",
        type=int,
        default=1701,
        help="first document seed; use a new value to force a cold prefix instead of an earlier run",
    )
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--output", default="/tmp/strata-long-context.json")
    args = parser.parse_args()

    key = args.key
    if args.key_file:
        if key:
            parser.error("use only one of --key and --key-file")
        key = read_key_file(args.key_file)
    slope, overhead = calibrate(args.base, key, args.timeout)
    print(f"calibration: {slope:.3f} tokens/word, {overhead:.1f} fixed tokens", flush=True)
    rows: list[dict] = []

    for index, target in enumerate(args.tokens):
        words = max(100, round((target - overhead) / slope))
        # A fixed default makes repeated benchmark rows comparable.  A caller
        # measuring a cold request can choose an unused base while the engine
        # still has other conversations parked.
        document = make_words(words, args.seed_base + index * 97)
        cold = chat(args.base, key, f"[long-context cold {index}]\n{document}\nSummarize the document.", args.timeout)
        report(f"{target // 1000}K cold", cold, rows)

        warm = chat(
            args.base,
            key,
            f"[long-context cold {index}]\n{document}\nSummarize the document.\nName one detail.",
            args.timeout,
        )
        report(f"{target // 1000}K warm suffix", warm, rows)

        # Rewrite near 25% of the document.  The bytes before the edit stay a
        # reusable prefix; the remaining suffix exercises checkpoint recovery.
        cut = len(document) // 4
        rewritten = document[:cut] + " rewritten-marker-" + document[cut:]
        rewritten_result = chat(
            args.base,
            key,
            f"[long-context cold {index}]\n{rewritten}\nSummarize the document.",
            args.timeout,
        )
        report(f"{target // 1000}K rewrite25", rewritten_result, rows)

    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2)
    good = [r for r in rows if r["ok"] and r["prompt_tps"] is not None]
    print(f"results: {args.output}; {len(good)}/{len(rows)} measured", flush=True)
    if good:
        print(f"median read throughput: {statistics.median(r['prompt_tps'] for r in good):.1f} tok/s", flush=True)
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
