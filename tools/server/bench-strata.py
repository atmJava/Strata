#!/usr/bin/env python3
"""Strata 调优基准：单请求 + 4 并发，结果追加到 jsonl。

为什么要 4 并发：`--batch` 的收益只在并发下才显现（单请求测不出来），
而 `--pool-workers` 的收益在两种负载下都可能不同。所以两个都测。

设计要点：
  - 固定提示词与 max_tokens，跨轮次完全一致，保证可比
  - temperature=0 消除采样随机性
  - 4 路用不同主题，让它们路由到不同专家（贴近真实多子代理场景）
  - 记录引擎自报的 pool_workers / batch_slots，验证配置确实生效
  - 记投机 draft 接受率：取 /metrics 累计计数在测量段前后的差（引擎每请求都写 drafts accepted，
    服务端存累计值；以前两样都没人读，L-011 那一列因此是空的），并写明窗口里有多少请求是别人的

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
    "用大约 400 字解释 speculative decoding（投机解码）如何在不改变输出的前提下加速。",    "用大约 400 字解释为什么 PCIe 4.0 x16 的双向带宽要按单向标称值的一半来算。",
    "用大约 400 字解释 NUMA 架构下跨节点访存为什么比本地访存慢。",
    "用大约 400 字解释为什么 batch size 越大，单 token 的边际延迟越低。",
    "用大约 400 字解释操作系统页缓存（page cache）对模型权重读取的影响。",
]

# BENCH_N: how many streams the concurrent arm runs (default 4, the M1 shape).  The capacity matrix
# (C1) needs 6 and 8 slots; the prompts repeat if N exceeds the topics.
N = int(os.environ.get("BENCH_N", "4"))
if not 1 <= N <= len(CONCURRENT_PROMPTS):
    raise SystemExit(f"BENCH_N must be 1..{len(CONCURRENT_PROMPTS)}, got {N}")


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
        "cached_tokens": (payload["usage"].get("prompt_tokens_details") or {}).get("cached_tokens"),
        "metrics": payload.get("metrics"),
        "timings": payload.get("timings"),
        "finish": payload.get("choices", [{}])[0].get("finish_reason") if payload.get("choices") else None,
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


def metrics_totals() -> dict:
    """读服务端的累计计数。计数在引擎启动时归零，所以两次读数的差就是中间发生的事——
    前提是 `since` 没动（中间重启过，差值就没有意义）。

    这就是基准终于能报投机 draft 接受率的办法：引擎每请求都写 `drafts accepted A of B`，
    服务端也存着累计值，但基准以前两样都不读，所以 L-011 那一列是空的。"""
    try:
        with urllib.request.urlopen(urllib.request.Request(METRICS, headers=AUTH), timeout=8) as response:
            data = json.loads(response.read().decode())
        totals = data.get("totals", {})
        return {
            "since": totals.get("since"),
            "requests": totals.get("requests"),
            "drafts_offered": totals.get("drafts_offered"),
            "drafts_accepted": totals.get("drafts_accepted"),
            "eng_tokens": totals.get("eng_tokens"),
            "prompt_tokens": totals.get("prompt_tokens"),
            "reused_tokens": totals.get("reused_tokens"),
        }
    except Exception as error:                       # noqa: BLE001
        return {"error": f"{type(error).__name__}: {error}"}


def acceptance_between(t0: dict, t1: dict, expected_requests: int) -> dict:
    """两次 /metrics 累计值之差，并把"这台机器上还有别人在跑"这件事写清楚。

    期望请求数 = 每轮 1 个单流 + N 个并发 = (1+N)×轮数。差值里的请求数若比这个大，
    多出来的就是别人的流量（很可能是驱动这次实验的那个 agent 会话自己），
    接受率就被污染了——所以这里明写 foreign_requests，而不是假装整段都是我们的。"""
    if "error" in t0 or "error" in t1:
        return {"error": t0.get("error") or t1.get("error")}
    if t0.get("since") != t1.get("since"):
        return {"error": "引擎中途重启过（since 变了），差值无意义"}
    offered = (t1["drafts_offered"] or 0) - (t0["drafts_offered"] or 0)
    accepted = (t1["drafts_accepted"] or 0) - (t0["drafts_accepted"] or 0)
    requests = (t1["requests"] or 0) - (t0["requests"] or 0)
    return {
        "drafts_offered": offered,
        "drafts_accepted": accepted,
        "acceptance_pct": round(100.0 * accepted / offered, 2) if offered else None,
        "requests_in_window": requests,
        "requests_from_this_bench": expected_requests,
        "foreign_requests": max(0, requests - expected_requests),
    }


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
    t0 = metrics_totals()                             # 从这里开始算累计差

    singles: list[float] = []
    aggregates: list[float] = []
    walls: list[float] = []
    slowest: list[float] = []
    single_runs: list[dict] = []
    concurrent_runs: list[list[dict]] = []
    for rep in range(1, reps + 1):
        print(f"  --- 第 {rep}/{reps} 轮 ---", flush=True)
        one = call(SINGLE_PROMPT)
        single_tok_s = one["tokens"] / one["wall_s"]
        singles.append(single_tok_s)
        single_runs.append(one)
        print(f"        单请求 {one['tokens']} tokens / {one['wall_s']:.1f}s = {single_tok_s:.1f} tok/s")

        started = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=N) as pool:
            futures = [pool.submit(call, p) for p in CONCURRENT_PROMPTS[:N]]
            runs = [f.result() for f in futures]
        wall = time.time() - started
        total_tokens = sum(r["tokens"] for r in runs)
        agg = total_tokens / wall
        aggregates.append(agg)
        walls.append(wall)
        slowest.append(max(r["wall_s"] for r in runs))
        concurrent_runs.append(runs)
        print(f"        {N} 并发 {total_tokens} tokens / 墙钟 {wall:.1f}s = {agg:.1f} tok/s 聚合"
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
    result["single_runs"] = single_runs
    result["concurrent_runs"] = concurrent_runs
    result["single_tok_s_median"] = round(median(singles), 1)
    result["concurrent_agg_tok_s_median"] = round(median(aggregates), 1)
    result["concurrent_wall_s_median"] = round(median(walls), 1)
    result["concurrent_slowest_s_median"] = round(median(slowest), 1)
    parallel_metrics = [run.get("metrics") or {} for rep in concurrent_runs for run in rep]
    parallel_timings = [run.get("timings") or {} for rep in concurrent_runs for run in rep]

    def metric_median(key: str) -> float | None:
        values = [row[key] for row in parallel_metrics if row.get(key) is not None]
        return round(median(values), 1) if values else None

    def timing_median(key: str) -> float | None:
        values = [row[key] for row in parallel_timings if row.get(key) is not None]
        return round(median(values), 1) if values else None

    result["parallel_metrics_median"] = {
        "ttft_ms": metric_median("ttft_ms"),
        "slot_wait_ms": metric_median("slot_wait_ms"),
        "wall_ms": metric_median("wall_ms"),
        "itl_ms_mean": metric_median("itl_ms_mean"),
        "prompt_ms": timing_median("prompt_ms"),
        "decode_ms": timing_median("predicted_ms"),
        "decode_tok_s": timing_median("predicted_per_second"),
    }
    print()
    print(f"  中位数：单请求 {result['single_tok_s_median']} tok/s | "
          f"{N} 并发聚合 {result['concurrent_agg_tok_s_median']} tok/s | "
          f"墙钟 {result['concurrent_wall_s_median']}s | 最慢单个 {result['concurrent_slowest_s_median']}s")
    pm = result["parallel_metrics_median"]
    print(f"  并发每请求中位：TTFT {pm['ttft_ms']} ms | slot wait {pm['slot_wait_ms']} ms | "
          f"prompt {pm['prompt_ms']} ms | decode {pm['decode_ms']} ms | {pm['decode_tok_s']} tok/s")

    t1 = metrics_totals()
    acc = acceptance_between(t0, t1, (1 + N) * reps)
    result["draft_acceptance"] = acc
    if "error" in acc:
        print(f"  投机 draft 接受率：读不到（{acc['error']}）")
    else:
        print(f"  投机 draft 接受率 {acc['acceptance_pct']}%（{acc['drafts_accepted']}/{acc['drafts_offered']}）"
              f"  窗口内请求 {acc['requests_in_window']}，其中本基准 {acc['requests_from_this_bench']}、"
              f"别人 {acc['foreign_requests']}")
        if acc["foreign_requests"]:
            print("        注：窗口里有别人的流量，接受率不是纯本基准的")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(f"  已追加到 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
