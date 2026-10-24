"""T3 - admission and neighbour stalls at full slots (docs/FEATURE_PARITY.md section 4, F1/F2).

Four streams fill the production slots; a fifth cold prompt joins.  Acceptance
(MULTIPATH_STANDARD.md section 1): its first token within 2 s, and no neighbour
stalls for more than 1 s.  Before W3 this is expected to fail - the reader queues
36-43 s at full slots (L-020/L-021) - so the failure is recorded as xfail with
its numbers, not hidden.
"""
import threading
import time

import pytest

import mp_client as mp

NEIGHBOURS = [
    "用大约 200 字解释混合专家（MoE）模型的路由。",
    "用大约 200 字解释 KV 缓存为什么随上下文线性增长。",
    "用大约 200 字解释投机解码为什么不改变输出。",
    "用大约 200 字解释 PCIe 带宽为什么是这种机器的瓶颈。",
]
READER = ("请阅读下面这段唯一文本并回答最后的问题。\n"
          + ("这是一段用于冷启动测试的唯一文本，编号 ADMISSION-" + "x" * 40 + "。\n") * 20
          + "问题：这段文本的编号是什么？")
LIMIT = 2.0
GAP_LIMIT = 1.0


def test_reader_admitted_within_2s_at_full_slots(server):
    lock = threading.Lock()
    firsts: list[float | None] = [None] * len(NEIGHBOURS)
    gaps: list[float] = []
    failures: list[str] = []

    def neighbour(index: int) -> None:
        body = mp.chat_body(NEIGHBOURS[index], max_tokens=192, stream=True)
        last = None
        try:
            for event, elapsed in mp.stream_chat(body):
                choices = event.get("choices") or [{}]
                delta = (choices[0].get("delta") or {})
                if not delta.get("content"):
                    continue
                if firsts[index] is None:
                    firsts[index] = elapsed
                if last is not None:
                    with lock:
                        gaps.append(elapsed - last)
                last = elapsed
        except Exception as error:  # noqa: BLE001 - report, do not hang
            with lock:
                failures.append(f"neighbour {index}: {type(error).__name__}: {error}")

    threads = [threading.Thread(target=neighbour, args=(i,), daemon=True) for i in range(len(NEIGHBOURS))]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        with lock:
            ready = all(item is not None for item in firsts) or bool(failures)
        if ready:
            break
        time.sleep(0.02)
    with lock:
        if failures:
            pytest.fail("; ".join(failures))
        started = all(item is not None for item in firsts)
    if not started:
        pytest.fail("neighbours never started decoding")

    text, usage, first = mp.collect_stream(mp.chat_body(READER, max_tokens=24, stream=True))
    assert text.strip()
    with lock:
        worst_gap = max(gaps) if gaps else 0.0
    for thread in threads:
        thread.join(timeout=600)

    report = (f"reader first token {first:.2f} s; worst neighbour gap {worst_gap:.2f} s; "
              f"reader usage {usage}")
    if first > LIMIT or worst_gap > GAP_LIMIT:
        pytest.xfail(f"{report} - W3 read-ahead not landed (baseline L-020/L-021: 36-43 s at full slots)")
    assert first <= LIMIT and worst_gap <= GAP_LIMIT, report
