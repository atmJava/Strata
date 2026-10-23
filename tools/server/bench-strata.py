#!/usr/bin/env python3
"""Strata 调优基准：单请求 + 4 并发，结果追加到 jsonl。

为什么要 4 并发：`--batch` 的收益只在并发下才显现（单请求测不出来），
而 `--pool-workers` 的收益在两种负载下都可能不同。所以两个都测。

设计要点：
  - 固定提示词与 max_tokens，跨轮次完全一致，保证可比
  - temperature=0 消除采样随机性
  - 4 路用不同主题，让它们路由到不同专家（贴近真实多子代理场景）
  - 记录引擎自报的 pool_workers / batch_slots，验证配置确实生效

用法：bench-strata.py <label>
"""
from __future__ import annotations

import concurrent.futures
import json
import pathlib
import sys
import time
import os
import urllib.request

URL = "http://127.0.0.1:8080/v1/chat/completions"
AUTH = {"Authorization": "Bearer " + os.environ.get("STRATA_API_KEY", "111")}
METRICS = "http://127.0.0.1:8080/metrics"
MODEL = "qwen3.8-flash-next-iq3_xxs"
MAX_TOKENS = 400
OUT = pathlib.Path("/opt/ai/logs/bench-results.jsonl")

SINGLE_PROMPT = "用大约 400 字解释混合专家（MoE）模型的工作原理，包括路由与负载均衡。"

# 四个不同主题 → 不同的专家路由，贴近真实多会话
CONCURRENT_PROMPTS = [
    "用大约 400 字解释混合专家（MoE）模型的工作原理，包括路由与负载均衡。",
    "用大约 400 字解释为什么 NVMe SSD 的随机读延迟比机械硬盘低两个数量级。",
    "用大约 400 字解释 Transformer 里 KV 缓存的作用，以及它为什么随上下文线性增长。",
    "用大约 400 字解释 speculative decoding（投机解码）如何在不改变输出的前提下加速。",
]


def call(prompt: str) -> dict:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
    }).encode()
    request = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json", **AUTH})
    started = time.time()
    with urllib.request.urlopen(request, timeout=900) as response:
        payload = json.loads(response.read().decode())
    return {
        "wall_s": time.time() - started,
        "tokens": payload["usage"]["completion_tokens"],
        "prompt_tokens": payload["usage"]["prompt_tokens"],
    }


def engine_info() -> dict:
    try:
        with urllib.request.urlopen(urllib.request.Request(METRICS, headers=AUTH), timeout=8) as response:
            data = json.loads(response.read().decode())
        engine = data.get("engine", {})
        live = data.get("live", {})
        return {
            "pool_workers": engine.get("pool_workers"),
            "batch_slots": engine.get("batch_slots"),
            "conversation_cache_mib": engine.get("conversation_cache_mib"),
            "vram_free_mib": engine.get("vram_free_mib"),
            "expert_slots": engine.get("expert_slots"),
            "expert_cache_mib": engine.get("expert_cache_mib"),
            "state": live.get("state"),
        }
    except Exception as error:                       # noqa: BLE001
        return {"error": f"{type(error).__name__}: {error}"}


WARMUP_PROMPT = "用一句话说明什么是 DNS 解析。"


def main(argv: list[str]) -> int:
    label = argv[0] if argv else "unlabeled"
    reps = int(argv[1]) if len(argv) > 1 else 3
    result: dict = {"label": label, "reps": reps,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    info = engine_info()
    result["engine"] = info
    print(f"  引擎: pool_workers={info.get('pool_workers')} batch_slots={info.get('batch_slots')} "
          f"expert_slots={info.get('expert_slots')} vram_free={info.get('vram_free_mib')}")

    # --- warmup：让专家/模型页缓存预热，且用无关提示词避免给被测提示词做缓存 ---
    print("  [warmup] 预热 …", flush=True)
    w = call(WARMUP_PROMPT)
    print(f"        预热完成 {w['tokens']} tokens / {w['wall_s']:.1f}s")

    singles: list[float] = []
    aggregates: list[float] = []
    walls: list[float] = []
    slowest: list[float] = []
    for rep in range(1, reps + 1):
        print(f"  --- 第 {rep}/{reps} 轮 ---", flush=True)
        one = call(SINGLE_PROMPT)
        single_tok_s = one["tokens"] / one["wall_s"]
        singles.append(single_tok_s)
        print(f"        单请求 {one['tokens']} tokens / {one['wall_s']:.1f}s = {single_tok_s:.1f} tok/s")

        started = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(call, p) for p in CONCURRENT_PROMPTS]
            runs = [f.result() for f in futures]
        wall = time.time() - started
        total_tokens = sum(r["tokens"] for r in runs)
        agg = total_tokens / wall
        aggregates.append(agg)
        walls.append(wall)
        slowest.append(max(r["wall_s"] for r in runs))
        print(f"        4 并发 {total_tokens} tokens / 墙钟 {wall:.1f}s = {agg:.1f} tok/s 聚合"
              f"  (最慢单个 {max(r['wall_s'] for r in runs):.1f}s)")
        time.sleep(2)

    def median(values: list[float]) -> float:
        ordered = sorted(values)
        mid = len(ordered) // 2
        return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2

    result["single_tok_s_each"] = [round(v, 1) for v in singles]
    result["concurrent_agg_tok_s_each"] = [round(v, 1) for v in aggregates]
    result["concurrent_wall_s_each"] = [round(v, 1) for v in walls]
    result["concurrent_slowest_s_each"] = [round(v, 1) for v in slowest]
    result["single_tok_s_median"] = round(median(singles), 1)
    result["concurrent_agg_tok_s_median"] = round(median(aggregates), 1)
    result["concurrent_wall_s_median"] = round(median(walls), 1)
    result["concurrent_slowest_s_median"] = round(median(slowest), 1)
    print()
    print(f"  中位数：单请求 {result['single_tok_s_median']} tok/s | "
          f"4 并发聚合 {result['concurrent_agg_tok_s_median']} tok/s | "
          f"墙钟 {result['concurrent_wall_s_median']}s | 最慢单个 {result['concurrent_slowest_s_median']}s")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(f"  已追加到 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
