#!/usr/bin/env python3
"""Run the frozen greedy parity probes against a local Strata server."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import time
import urllib.error
import urllib.request


PROBES = (
    (
        "Reply with exactly STRATA_PARITY_ALPHA and nothing else.",
        "STRATA_PARITY_ALPHA",
    ),
    (
        "Compute 59 * 16 and reply with only the result.",
        "944",
    ),
    (
        "Return exactly The quick brown fox jumps over the lazy dog. and nothing else.",
        "The quick brown fox jumps over the lazy dog.",
    ),
)


def request_chat(base: str, key: str, model: str, prompt: str) -> dict:
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 96,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(
        base + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            answer = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    choice = answer["choices"][0]
    text = choice.get("message", {}).get("content") or ""
    usage = answer.get("usage") or {}
    return {
        "text": text,
        "finish": choice.get("finish_reason"),
        "completion_tokens": usage.get("completion_tokens"),
        "wall_s": round(time.monotonic() - started, 3),
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=pathlib.Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()

    config = json.loads(args.config.read_text())
    base = f"http://127.0.0.1:{int(config.get('port') or 8080)}"
    key = config.get("api_key") or ""
    model = config["model_name"]

    for prompt, _ in PROBES:
        request_chat(base, key, model, prompt)

    rows = []
    passed = True
    for trial in range(2):
        for probe, (prompt, expected) in enumerate(PROBES):
            row = request_chat(base, key, model, prompt)
            row.update(
                probe=probe,
                trial=trial,
                expected=expected,
                exact=row["text"] == expected,
                finish_ok=row["finish"] == "stop",
            )
            passed = passed and row["exact"] and row["finish_ok"]
            rows.append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"run": args.label, "passed": passed, "rows": rows}, indent=2, sort_keys=True) + "\n"
    )
    for probe in range(len(PROBES)):
        samples = [row for row in rows if row["probe"] == probe]
        repeatable = len({row["sha256"] for row in samples}) == 1
        finishes = {row["finish"] for row in samples}
        print(
            f"probe {probe}: repeatable={repeatable} finishes={sorted(finishes)} "
            f"exact={all(row['exact'] for row in samples)}"
        )
    print(f"passed={passed} output={args.output}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
