#!/usr/bin/env python3
"""Measure a cold request that arrives after every engine batch slot is decoding.

The probe fills the configured number of slots with streaming decodes, waits until
``/metrics`` says all of them are non-idle, and then sends one unique short reader.
It reports both clocks:

* server ``queue_ms``, ``ttft_ms``, ``wall_ms`` from the final ``done`` event;
* client time to first and second content token;
* the longest silence in each neighbour stream while the reader is waiting and running.

By default the reader prompt is below production's 4096-token staged-read
threshold: this measures the ordinary wait-for-a-slot path.  A non-zero
``--reader-prompt-tokens`` approximates that length instead, so the same probe
can compare the staged-read threshold itself.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid


def max_silence(times: list[float], lo: float, hi: float) -> float:
    """Longest empty interval in ``[lo, hi]``, treating both ends as events."""
    if hi < lo:
        return 0.0
    points = [lo] + sorted(t for t in times if lo < t < hi) + [hi]
    return max((b - a for a, b in zip(points, points[1:])), default=0.0)


def max_gap(times: list[float], lo: float | None = None, hi: float | None = None) -> float:
    """Longest consecutive event gap, optionally clipped to ``[lo, hi]``."""
    if lo is not None:
        times = [t for t in times if t >= lo]
    if hi is not None:
        times = [t for t in times if t <= hi]
    return max((b - a for a, b in zip(times, times[1:])), default=0.0)


def busy_slots(states: list[dict]) -> tuple[int, int]:
    busy = sum(1 for state in states if state.get("state", "idle") != "idle")
    decoding = sum(1 for state in states if state.get("state") == "decoding")
    return busy, decoding


def reader_prompt(marker: str, target_tokens: int) -> str:
    """A unique reader prompt of roughly target_tokens for staged-read threshold tests."""
    if target_tokens <= 0:
        return (
            f"Cold admission probe {marker}. Count from 1 to 12, separated by spaces. "
            "Do not explain."
        )
    lines = [f"Cold admission probe {marker}."]
    lines.extend(
        "alpha bravo charlie delta echo foxtrot golf hotel kilo lima mike november."
        for _ in range(max(32, int(target_tokens / 12)))
    )
    lines.append("Count from 1 to 12, separated by spaces. Do not explain.")
    return "\n".join(lines)


class Client:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

    def metrics(self) -> dict:
        request = urllib.request.Request(self.base_url + "/metrics", headers=self._headers())
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def stream(self, prompt: str, max_tokens: int, out: dict) -> None:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "top_p": 1,
            "reasoning_effort": "none",
            "chat_template_kwargs": {"enable_thinking": False},
            "stream": True,
        }
        request = urllib.request.Request(
            self.base_url + "/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers=self._headers(),
        )
        started = time.monotonic()
        # Publish arrival stamps while the stream is live.  Waiting until EOF makes
        # the main thread miss the entire full-slot window and turns a short decode
        # into a false "gate never saw enough requests" failure.
        content_times: list[float] = out.setdefault("times", [])
        chars = 0
        usage = None
        metrics = None
        finish = None
        out.update(start=started)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    usage = event.get("usage") or usage
                    metrics = event.get("metrics") or metrics
                    done = event.get("done")
                    if isinstance(done, dict):
                        usage = done.get("usage") or usage
                        metrics = done.get("metrics") or metrics
                        finish = done.get("finish") or finish
                    if metrics is not None:
                        # The final usage/metrics event is the last event this server sends.  Some
                        # proxies keep an SSE connection open instead of forwarding [DONE], so do
                        # not make the probe depend on reading EOF after the measurements arrived.
                        break
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content") or ""
                    if text:
                        content_times.append(time.monotonic())
                        chars += len(text)
        except Exception as error:  # noqa: BLE001 - retain a failed round instead of hanging
            out["error"] = f"{type(error).__name__}: {error}"
        out.update(
            end=time.monotonic(),
            times=content_times,
            chars=chars,
            usage=usage,
            metrics=metrics,
            finish=finish,
        )


def wait_for_slots(client: Client, target: int, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    live: dict = {}
    max_busy = max_running = 0
    last_seen = None
    while time.monotonic() < deadline:
        live = client.metrics().get("live", {})
        states = live.get("slots", [])
        busy, decoding = busy_slots(states)
        running = int(live.get("running") or 0)
        max_busy = max(max_busy, busy)
        max_running = max(max_running, running)
        seen = (busy, decoding, running)
        if seen != last_seen:
            print(f"gate: busy={busy} decoding={decoding} running={running}", flush=True)
            last_seen = seen
        # Pipelined batch groups need not decode every slot at the same instant:
        # a slot can be occupied while its group overlaps the other group's work.
        # The deployed metrics can also lag the slot view while ``running`` already
        # counts every in-flight request, so accept either complete signal.
        if busy >= target or running >= target:
            return live
        time.sleep(0.1)
    states = live.get("slots", [])
    busy, decoding = busy_slots(states)
    raise RuntimeError(
        f"never reached {target} in-flight requests within {timeout:.0f} s "
        f"(max busy={max_busy}, max running={max_running}, last busy={busy}, last decoding={decoding})"
    )


def wait_for_idle(client: Client, timeout: float) -> list[dict]:
    deadline = time.monotonic() + timeout
    states: list[dict] = []
    while time.monotonic() < deadline:
        states = client.metrics().get("live", {}).get("slots", [])
        if not any(state.get("state", "idle") != "idle" for state in states):
            return states
        time.sleep(0.1)
    raise RuntimeError(f"slots did not become idle within {timeout:.0f} s: {states}")


def monitor_reader_slot(client: Client, reader: dict, target: int, stop: threading.Event,
                        result: dict) -> None:
    """Record when the late request leaves ``outside_slots`` and gets an engine slot."""
    saw_extra = False
    max_running = 0
    max_outside = 0
    while not stop.is_set():
        start = reader.get("start")
        if start is None:
            time.sleep(0.01)
            continue
        try:
            live = client.metrics().get("live", {})
        except Exception as error:  # noqa: BLE001 - retain the failure in the round
            result["error"] = f"{type(error).__name__}: {error}"
            return
        running = int(live.get("running") or 0)
        outside = int(live.get("outside_slots") or 0)
        max_running = max(max_running, running)
        max_outside = max(max_outside, outside)
        result.update(max_running=max_running, max_outside=max_outside)
        if running > target:
            saw_extra = True
        if saw_extra and outside == 0:
            result.update(
                slot_wait_s=time.monotonic() - start,
                saw_extra=True,
                slots_when_admitted=live.get("slots", []),
            )
            return
        if reader.get("end") is not None:
            break
        time.sleep(0.02)
    result.setdefault("saw_extra", saw_extra)


def run_round(client: Client, target: int, neighbour_tokens: int, reader_tokens: int,
              reader_prompt_tokens: int, timeout: float, round_number: int) -> dict:
    marker = uuid.uuid4().hex
    neighbours = [{} for _ in range(target)]
    threads = []
    for index in range(target):
        prompt = (
            f"Probe round {round_number}, neighbour {index}, nonce {marker}. "
            "Write a detailed technical explanation of mixture-of-experts routing, capacity factors, "
            "load balancing and tail latency. Continue until the token limit."
        )
        thread = threading.Thread(
            target=client.stream,
            args=(prompt, neighbour_tokens, neighbours[index]),
            daemon=True,
        )
        threads.append(thread)
        thread.start()

    deadline = time.monotonic() + timeout
    started_neighbours = 0
    while time.monotonic() < deadline:
        if any(item.get("error") for item in neighbours):
            break
        ready = sum(1 for item in neighbours if item.get("times"))
        if ready != started_neighbours:
            print(f"neighbours with first content: {ready}/{target}", flush=True)
            started_neighbours = ready
        if ready == target:
            break
        time.sleep(0.05)

    errors = [item["error"] for item in neighbours if item.get("error")]
    if errors:
        for thread in threads:
            thread.join(timeout=timeout)
        raise RuntimeError("neighbour failed before the reader started: " + "; ".join(errors))
    if not all(item.get("times") for item in neighbours):
        for thread in threads:
            thread.join(timeout=timeout)
        raise RuntimeError("not every neighbour reached decoding before the reader started")
    live = wait_for_slots(client, target, timeout)
    states = live.get("slots", [])

    reader = {}
    prompt = reader_prompt(marker, reader_prompt_tokens)
    admission: dict = {}
    admission_stop = threading.Event()
    reader_thread = threading.Thread(
        target=client.stream,
        args=(prompt, reader_tokens, reader),
        daemon=True,
    )
    reader_thread.start()
    admission_thread = threading.Thread(
        target=monitor_reader_slot,
        args=(client, reader, target, admission_stop, admission),
        daemon=True,
    )
    admission_thread.start()

    for thread in threads:
        thread.join(timeout=timeout)
    reader_thread.join(timeout=timeout)
    admission_stop.set()
    admission_thread.join(timeout=10)
    if any(thread.is_alive() for thread in threads) or reader_thread.is_alive():
        raise RuntimeError("a probe stream stayed open after the measurement deadline")
    if reader.get("end") is None:
        raise RuntimeError("reader did not finish within the probe timeout")
    if reader.get("error"):
        raise RuntimeError("reader failed: " + reader["error"])

    reader_times = reader.get("times") or []
    reader_start = reader["start"]
    reader_end = reader["end"]
    first = reader_times[0] - reader_start if reader_times else None
    second = reader_times[1] - reader_start if len(reader_times) > 1 else None
    stall = (reader_times[1] - reader_times[0]) if len(reader_times) > 1 else None

    silence_waits: list[float] = []
    silence_windows: list[float] = []
    gaps: list[float] = []
    for item in neighbours:
        times = item.get("times") or []
        if not times:
            continue
        wait_hi = min(first + reader_start if first is not None else reader_end, times[-1])
        silence_waits.append(max_silence(times, reader_start, wait_hi))
        silence_windows.append(max_silence(times, reader_start, min(reader_end, times[-1])))
        gaps.append(max_gap(times))

    row = {
        "round": round_number,
        "server_running": live.get("running"),
        "server_outside_slots": live.get("outside_slots"),
        "slots_busy": busy_slots(states)[0],
        "slots_decoding": busy_slots(states)[1],
        "reader_client_ttft_s": round(first, 3) if first is not None else None,
        "reader_client_second_s": round(second, 3) if second is not None else None,
        "reader_client_stall_s": round(stall, 3) if stall is not None else None,
        "reader_client_wall_s": round(reader_end - reader_start, 3),
        "reader_slot_wait_s": round(admission["slot_wait_s"], 3)
        if admission.get("slot_wait_s") is not None else None,
        "reader_admission_observed": bool(admission.get("slot_wait_s") is not None),
        "reader_max_running": admission.get("max_running"),
        "reader_max_outside_slots": admission.get("max_outside"),
        "reader_metrics": reader.get("metrics"),
        "reader_prompt_tokens": int((reader.get("usage") or {}).get("prompt_tokens") or 0),
        "reader_chars": reader.get("chars"),
        "reader_finish": reader.get("finish"),
        "neighbour_silence_until_reader_ttft_max_s": round(max(silence_waits, default=0.0), 3),
        "neighbour_silence_in_reader_window_max_s": round(max(silence_windows, default=0.0), 3),
        "neighbour_gap_overall_max_s": round(max(gaps, default=0.0), 3),
    }
    wait_for_idle(client, timeout)
    return row


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("MP_BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--api-key", default=os.environ.get("MP_API_KEY"))
    parser.add_argument("--model", default=os.environ.get("MP_MODEL"))
    parser.add_argument("--slots", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--neighbour-tokens", type=int, default=400)
    parser.add_argument("--reader-tokens", type=int, default=16)
    parser.add_argument(
        "--reader-prompt-tokens", type=int, default=0,
        help="approximate cold prompt size for the late reader (0 keeps the short prompt)",
    )
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    if not args.api_key:
        parser.error("set MP_API_KEY or pass --api-key")
    if not args.model:
        parser.error("set MP_MODEL or pass --model")
    if args.slots < 2 or args.rounds < 1:
        parser.error("--slots must be at least 2 and --rounds at least 1")

    client = Client(args.base_url, args.api_key, args.model, args.timeout)
    wait_for_idle(client, args.timeout)
    rows = []
    for number in range(1, args.rounds + 1):
        row = run_round(
            client, args.slots, args.neighbour_tokens, args.reader_tokens,
            args.reader_prompt_tokens, args.timeout, number,
        )
        rows.append(row)
        metrics = row.get("reader_metrics") or {}
        print(
            f"round {number}: prompt={row['reader_prompt_tokens']} tokens, "
            f"slot-wait={row['reader_slot_wait_s']} s, "
            f"metrics-queue={metrics.get('queue_ms')} ms, ttft={metrics.get('ttft_ms')} ms, "
            f"wall={metrics.get('wall_ms')} ms, second-token stall={row['reader_client_stall_s']} s, "
            f"neighbour silence={row['neighbour_silence_in_reader_window_max_s']} s",
            flush=True,
        )

    def median(key: str) -> float | None:
        values = [row[key] for row in rows if row.get(key) is not None]
        return statistics.median(values) if values else None

    summary = {
        "rows": rows,
        "median_reader_prompt_tokens": median("reader_prompt_tokens"),
        "median_queue_ms": median_metric(rows, "queue_ms"),
        "median_slot_wait_s": median("reader_slot_wait_s"),
        "median_ttft_ms": median_metric(rows, "ttft_ms"),
        "median_wall_ms": median_metric(rows, "wall_ms"),
        "median_second_token_stall_s": median("reader_client_stall_s"),
        "median_neighbour_silence_until_ttft_s": median("neighbour_silence_until_reader_ttft_max_s"),
        "median_neighbour_silence_in_reader_window_s": median("neighbour_silence_in_reader_window_max_s"),
    }
    print(json.dumps(summary, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
        print(f"json: {args.out}")
    return 0


def median_metric(rows: list[dict], key: str) -> float | None:
    values = [
        row["reader_metrics"][key]
        for row in rows
        if row.get("reader_metrics") and row["reader_metrics"].get(key) is not None
    ]
    return statistics.median(values) if values else None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
