#!/usr/bin/env python3
"""冻结并审计 Flash-Next G0 请求样本。

本工具只读取服务端 `/metrics?requests=all`，不发送生成请求。`capture` 保存原始响应，
`audit` 用固定的请求形状规则重新分类噪声、重算聚合值和分位数，并与 G0 证据交叉校验。
"""
from __future__ import annotations

import argparse
import datetime as dt
import decimal
import json
import math
import os
import re
import statistics
import tempfile
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_METRICS = ROOT / "docs/work/evidence/flash-next-runtime-g0-requests-20261030.json"
DEFAULT_EVIDENCE = ROOT / "docs/work/evidence/flash-next-runtime-g0-20261030.json"
REQUEST_ID = re.compile(r"[0-9a-f]{12}")


class AuditError(RuntimeError):
    """证据结构与请求快照不能支持一次可信审计。"""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def request_id(row: dict) -> str:
    value = row.get("id")
    if not isinstance(value, str) or REQUEST_ID.fullmatch(value) is None:
        raise AuditError(f"请求 ID 非法: {value!r}")
    return value


def classify_noise(row: dict) -> str | None:
    """按固定请求形状分类 G0 启动与探针噪声，不读取提示词或回答内容。"""
    prompt = int(row.get("prompt_tokens") or 0)
    output = int(row.get("output_tokens") or 0)
    if prompt == 17 and output <= 2:
        return "startup_warmup"
    if prompt < 100 and output == 400:
        return "fixed_400_token_probe"
    if prompt < 100 and output < 100:
        return "short_smoke"
    return None


def percentile(values: list[float], q: float) -> float | None:
    """线性插值分位数，与 G0 证据使用的口径一致。"""
    if not values:
        return None
    if not 0 <= q <= 1:
        raise ValueError("q must be between 0 and 1")
    ordered = sorted(decimal.Decimal(str(value)) for value in values)
    position = decimal.Decimal(len(ordered) - 1) * decimal.Decimal(str(q))
    lower = int(position.to_integral_value(rounding=decimal.ROUND_FLOOR))
    upper = int(position.to_integral_value(rounding=decimal.ROUND_CEILING))
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def round_half_up(value: float, digits: int) -> float:
    """按证据口径四舍五入，避免 Python round 的二进制浮点偶舍入。"""
    quantum = decimal.Decimal(1).scaleb(-digits)
    return float(decimal.Decimal(str(value)).quantize(quantum, rounding=decimal.ROUND_HALF_UP))


def distribution(rows: list[dict], key: str, digits: int | None = None) -> dict[str, float | int] | None:
    values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
    if not values:
        return None

    def shape(value: float) -> float | int:
        if digits is None:
            return int(value) if value.is_integer() else value
        return round_half_up(value, digits)

    return {
        "min": shape(min(values)),
        "p10": shape(percentile(values, 0.10)),
        "p50": shape(percentile(values, 0.50)),
        "p90": shape(percentile(values, 0.90)),
        "p99": shape(percentile(values, 0.99)),
        "max": shape(max(values)),
    }


def close(actual: float, expected: float, tolerance: float = 0.05) -> bool:
    return abs(float(actual) - float(expected)) <= tolerance


def compare_shape(errors: list[str], label: str, actual: dict | None, expected: dict | None,
                  tolerance: float = 0.05) -> None:
    if actual is None or not isinstance(expected, dict):
        errors.append(f"{label}: 缺少实际值或证据值")
        return
    for key, want in expected.items():
        if key not in actual or not isinstance(want, (int, float)):
            errors.append(f"{label}.{key}: 证据字段缺失或不是数字")
        elif not close(actual[key], want, tolerance):
            errors.append(f"{label}.{key}: 重算 {actual[key]} != 证据 {want}")


def metric_rows(snapshot: dict) -> list[dict]:
    payload = snapshot.get("payload", snapshot)
    rows = payload.get("requests") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise AuditError("请求快照没有非空 payload.requests")
    if any(not isinstance(row, dict) for row in rows):
        raise AuditError("payload.requests 含有非对象行")
    return rows


def audit(raw: dict, evidence: dict) -> dict:
    """重新分类、重算并校验一份原始请求快照。"""
    rows = metric_rows(raw)
    errors: list[str] = []
    by_id: dict[str, str] = {}
    for row in rows:
        rid = request_id(row)
        if rid in by_id:
            errors.append(f"重复请求 ID: {rid}")
        reason = classify_noise(row)
        if reason:
            by_id[rid] = reason
    excluded = set(by_id)
    production = [row for row in rows if request_id(row) not in excluded]

    source = evidence.get("requests") or {}
    snap = source.get("snapshot") or {}
    noise = source.get("noise_classification") or {}
    if snap.get("returned") != len(rows):
        errors.append(f"原始数：快照 {len(rows)}，证据 {snap.get('returned')}")
    if snap.get("requests_kept") != len(rows):
        errors.append(f"保留数：快照 {len(rows)}，证据 {snap.get('requests_kept')}")
    expected_ids = set(noise.get("excluded_ids") or [])
    if excluded != expected_ids:
        errors.append(f"剔除 ID 不一致：多 {sorted(excluded - expected_ids)}，少 {sorted(expected_ids - excluded)}")
    expected_by_reason = {
        reason: set(ids or []) for reason, ids in (noise.get("excluded_reasons") or {}).items()
    }
    actual_by_reason: dict[str, set[str]] = {}
    for rid, reason in by_id.items():
        actual_by_reason.setdefault(reason, set()).add(rid)
    for reason in sorted(set(expected_by_reason) | set(actual_by_reason)):
        if expected_by_reason.get(reason, set()) != actual_by_reason.get(reason, set()):
            errors.append(f"{reason}: 剔除 ID 与证据不一致")
    if noise.get("excluded_server_visible_count") != len(excluded):
        errors.append("excluded_server_visible_count 与重算不一致")
    if noise.get("production_count") != len(production):
        errors.append(f"生产数：重算 {len(production)}，证据 {noise.get('production_count')}")

    finishes: dict[str, int] = {}
    for row in production:
        finish = str(row.get("finish") or "missing")
        finishes[finish] = finishes.get(finish, 0) + 1
    prompt_sum = sum(int(row.get("prompt_tokens") or 0) for row in production)
    reused_sum = sum(int(row.get("reused") or 0) for row in production)
    read_sum = sum(int(row.get("prompt_read") or 0) for row in production)
    output_sum = sum(int(row.get("output_tokens") or 0) for row in production)
    generated_sum = sum(int(row.get("engine_generated") or 0) for row in production)
    prompt_ms_sum = sum(float(row.get("prompt_ms") or 0.0) for row in production)
    decode_ms_sum = sum(float(row.get("decode_ms") or 0.0) for row in production)

    aggregates = {
        "n": len(production),
        "finish": finishes,
        "prompt_tokens_sum": prompt_sum,
        "reused_sum": reused_sum,
        "reused_ratio": round(reused_sum / prompt_sum, 4) if prompt_sum else None,
        "prompt_read_sum": read_sum,
        "output_tokens_sum": output_sum,
        "engine_generated_sum": generated_sum,
        "output_to_engine_generated_ratio": round(output_sum / generated_sum, 3) if generated_sum else None,
        "prompt_ms_sum": round(prompt_ms_sum, 1),
        "decode_ms_sum": round(decode_ms_sum, 1),
        "aggregate_read_tokens_per_s": round(read_sum / (prompt_ms_sum / 1000), 1) if prompt_ms_sum else None,
        "aggregate_committed_tokens_over_engine_decode_s":
            round(output_sum / (decode_ms_sum / 1000), 1) if decode_ms_sum else None,
    }
    expected_aggregate = source.get("production_aggregate") or {}
    for key, actual in aggregates.items():
        want = expected_aggregate.get(key)
        if key == "finish":
            if actual != want:
                errors.append(f"finish 分布：重算 {actual}，证据 {want}")
        elif key == "reused_ratio" and actual is not None and isinstance(want, (int, float)):
            if not close(actual, want, 0.00005):
                errors.append(f"reused_ratio：重算 {actual}，证据 {want}")
        elif key in ("output_to_engine_generated_ratio",) and actual is not None and isinstance(want, (int, float)):
            if not close(actual, want, 0.0005):
                errors.append(f"{key}：重算 {actual}，证据 {want}")
        elif isinstance(actual, float) and isinstance(want, (int, float)):
            if not close(actual, want, 0.11):
                errors.append(f"{key}：重算 {actual}，证据 {want}")
        elif actual != want:
            errors.append(f"{key}：重算 {actual}，证据 {want}")

    for row in production:
        generated = int(row.get("engine_generated") or 0)
        decode_ms = float(row.get("decode_ms") or 0.0)
        output = int(row.get("output_tokens") or 0)
        first = row.get("first_engine_token_s")
        duration = row.get("duration_s")
        if generated and decode_ms:
            row["_engine_reported_decode_tok_s"] = round(generated / (decode_ms / 1000), 1)
        if output and decode_ms:
            row["_committed_tokens_over_engine_decode_tok_s"] = output / (decode_ms / 1000)
        if output and isinstance(first, (int, float)) and isinstance(duration, (int, float)) \
                and duration > first:
            row["_committed_tokens_over_wall_after_engine_first_token"] = output / (duration - first)
        if output and isinstance(duration, (int, float)) and duration > 0:
            row["_committed_tokens_over_total_wall"] = output / duration

    distributions = {
        "prompt_tokens": distribution(production, "prompt_tokens"),
        "reused": distribution(production, "reused"),
        "prompt_read": distribution(production, "prompt_read"),
        "output_tokens": distribution(production, "output_tokens"),
        "duration_s": distribution(production, "duration_s", 1),
        "first_engine_token_s": distribution(production, "first_engine_token_s", 2),
        "engine_reported_decode_tok_s": distribution(production, "_engine_reported_decode_tok_s", 2),
        "committed_tokens_over_engine_decode_tok_s":
            distribution(production, "_committed_tokens_over_engine_decode_tok_s", 2),
        "committed_tokens_over_wall_after_engine_first_token":
            distribution(production, "_committed_tokens_over_wall_after_engine_first_token", 2),
        "committed_tokens_over_total_wall": distribution(production, "_committed_tokens_over_total_wall", 2),
        "expert_cache_hit_rate": distribution(production, "hit_rate", 4),
        "pcie_offloaded_share": distribution(production, "pcie_share", 4),
    }
    expected_distribution = source.get("production_distribution") or {}
    tolerances = {
        "prompt_tokens": 0.1, "reused": 0.1, "prompt_read": 0.1, "output_tokens": 0.1,
        "duration_s": 0.06, "first_engine_token_s": 0.006,
        "engine_reported_decode_tok_s": 0.1,
        "committed_tokens_over_engine_decode_tok_s": 0.1,
        "committed_tokens_over_wall_after_engine_first_token": 0.1,
        "committed_tokens_over_total_wall": 0.1,
        "expert_cache_hit_rate": 0.00006, "pcie_offloaded_share": 0.00006,
    }
    for key, actual in distributions.items():
        compare_shape(errors, key, actual, expected_distribution.get(key), tolerances[key])

    for row in source.get("major_samples") or []:
        rid = row.get("id")
        if rid in excluded:
            errors.append(f"主要生产样本被误剔除: {rid}")

    production.sort(key=lambda row: float(row.get("time") or 0.0))
    return {
        "ok": not errors,
        "errors": errors,
        "raw_count": len(rows),
        "excluded_count": len(excluded),
        "excluded_ids": sorted(excluded),
        "excluded_reasons": {key: sorted(value) for key, value in sorted(actual_by_reason.items())},
        "production_count": len(production),
        "production_ids": [request_id(row) for row in production],
        "aggregates": aggregates,
        "distributions": distributions,
    }


def capture(base_url: str, api_key: str, output: Path) -> dict:
    """读取完整请求账本并以原子方式保存原始响应。"""
    request = urllib.request.Request(base_url.rstrip("/") + "/metrics?requests=all",
                                     headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.load(response)
    snapshot = {
        "schema_version": 1,
        "captured_at_utc": utc_now(),
        "source": base_url.rstrip("/") + "/metrics?requests=all",
        "payload": payload,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, output)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture", help="只读抓取 /metrics?requests=all")
    cap.add_argument("--base-url", default="http://127.0.0.1:8080")
    cap.add_argument("--api-key", default=os.environ.get("STRATA_API_KEY", "111"))
    cap.add_argument("--output", type=Path, default=DEFAULT_METRICS)
    audit_parser = sub.add_parser("audit", help="重算分类、聚合和分位数")
    audit_parser.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    audit_parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    audit_parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "capture":
        snapshot = capture(args.base_url, args.api_key, args.output)
        print(f"已保存 {len(metric_rows(snapshot))} 条原始请求 -> {args.output}")
        return 0

    raw = json.loads(args.metrics.read_text(encoding="utf-8"))
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    report = audit(raw, evidence)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"原始 {report['raw_count']}，剔除 {report['excluded_count']}，生产 {report['production_count']}")
        for reason, ids in report["excluded_reasons"].items():
            print(f"  {reason}: {len(ids)} ({', '.join(ids)})")
        for key, value in report["aggregates"].items():
            print(f"  {key}: {value}")
        if report["errors"]:
            for error in report["errors"]:
                print(f"FAIL: {error}")
        else:
            print("PASS: 分类、公式和分位数与 G0 证据一致")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
