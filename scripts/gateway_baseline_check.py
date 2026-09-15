#!/usr/bin/env python
"""gateway_baseline_check.py — 网关迁移基线检查（B0，只读探针）

用途：
  固化"前端 rewrites + FastAPI 直连"现状行为，产出结构化 JSON，
  供 APISIX 入口（B1/B2）与直连现状逐项比对。默认只跑无副作用探针；
  SSE / 上传等会触发真实 LLM 调用或大文件 IO 的探针必须显式开启。

用法：
  # 录制直连基线（默认目标 FastAPI 8000）
  python scripts/gateway_baseline_check.py --base http://127.0.0.1:8000 --record baseline_fastapi.json

  # 比对 APISIX 入口（B1 起使用）
  python scripts/gateway_baseline_check.py --base http://127.0.0.1:9080 --compare baseline_fastapi.json

设计约定：
  - 默认探针全部只读：health / 首页 / openapi / metrics / OPTIONS 预检 / 404 形状 /
    未带凭据访问保护端点的拒绝行为（本就是拒绝，无副作用）。
  - --with-sse / --with-upload 属于"需真实服务"的重量级探针，默认关闭。
  - 任何异常都被记录为该探针的 error 字段（含 traceback），绝不静默吞错；
    没有 except: pass。
  - API_KEY 从环境变量读取（与 FastAPI 同名配置），不写入任何文件。

退出码：比对模式存在差异 → 1；录制模式探针存在 error → 1；其余 0。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from urllib import error, request

DEFAULT_TIMEOUT = 10.0
USER_AGENT = "gateway-baseline/1.0"

# 未带凭据时允许出现的拒绝形态（fail-closed 503 或 401 均合法，但必须二选一且稳定）
REJECT_STATUSES = {401, 503}


def _fetch(method: str, url: str, *, headers: dict | None = None,
           body: bytes | None = None, timeout: float = DEFAULT_TIMEOUT):
    """单次 HTTP 请求。返回 (status, headers_dict, body_bytes, elapsed_ms)。
    4xx/5xx 不视为异常——状态码本身就是探针结果。"""
    req = request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    req.add_header("User-Agent", USER_AGENT)
    start = time.monotonic()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            elapsed = (time.monotonic() - start) * 1000
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, payload, elapsed
    except error.HTTPError as e:  # 4xx/5xx：属预期内的"结果"，不是探针故障
        payload = e.read()
        elapsed = (time.monotonic() - start) * 1000
        return e.code, {k.lower(): v for k, v in e.headers.items()}, payload, elapsed


def probe(name: str):
    """探针装饰器：捕获一切异常记入结果，不中断后续探针。"""
    def deco(fn):
        def wrapper(ctx, results):
            entry: dict = {"name": name, "status": "ok"}
            try:
                detail = fn(ctx) or {}
                entry.update(detail)
            except Exception:
                entry["status"] = "error"
                entry["error"] = traceback.format_exc()
            results.append(entry)
            return entry
        return wrapper
    return deco


# ── 默认（只读）探针 ──────────────────────────────────────────

@probe("health")
def _p_health(ctx):
    status, headers, body, ms = _fetch("GET", ctx["base"] + "/health")
    parsed = json.loads(body.decode("utf-8", "replace")) if body[:1] == b"{" else None
    return {"http_status": status, "latency_ms": round(ms, 1),
            "body_sample": (body[:200].decode("utf-8", "replace")),
            "json_keys": sorted(parsed.keys()) if isinstance(parsed, dict) else None,
            "assert": status == 200}


@probe("index_self_describe")
def _p_index(ctx):
    status, headers, body, ms = _fetch("GET", ctx["base"] + "/")
    return {"http_status": status, "latency_ms": round(ms, 1),
            "body_sample": body[:200].decode("utf-8", "replace"),
            "assert": status == 200}


@probe("openapi_route_inventory")
def _p_openapi(ctx):
    """抓取 openapi.json：既是探针也是权威路由清单来源。"""
    status, headers, body, ms = _fetch("GET", ctx["base"] + "/openapi.json")
    spec = json.loads(body.decode("utf-8", "replace")) if status == 200 else {}
    paths = spec.get("paths", {})

    def _prefix(p: str) -> str:
        parts = p.strip("/").split("/")
        return "/" + parts[1] if len(parts) >= 2 and parts[1] else ("/" + parts[0] if parts[0] else "/")

    prefixes = sorted({_prefix(p) for p in paths})
    return {"http_status": status, "path_count": len(paths),
            "first_level_prefixes": prefixes,
            "assert": status == 200 and len(paths) > 0}


@probe("metrics_unauthenticated")
def _p_metrics(ctx):
    status, headers, body, ms = _fetch("GET", ctx["base"] + "/metrics")
    return {"http_status": status, "latency_ms": round(ms, 1),
            "content_type": headers.get("content-type", ""),
            "assert": status == 200}


@probe("options_preflight_cors")
def _p_preflight(ctx):
    """CORS 预检：验证 CORS 中间件在直连时的响应头形状。"""
    status, headers, body, ms = _fetch(
        "OPTIONS", ctx["base"] + "/api/chat/stream",
        headers={"Origin": ctx["origin"], "Access-Control-Request-Method": "POST"})
    return {"http_status": status,
            "allow_origin": headers.get("access-control-allow-origin", ""),
            "allow_headers": headers.get("access-control-allow-headers", ""),
            "allow_methods": headers.get("access-control-allow-methods", ""),
            "allow_credentials": headers.get("access-control-allow-credentials", ""),
            "assert": status in (200, 204) and bool(headers.get("access-control-allow-origin"))}


@probe("not_found_shape")
def _p_404(ctx):
    status, headers, body, ms = _fetch("GET", ctx["base"] + "/__baseline_nonexistent__")
    parsed = None
    if body[:1] == b"{":
        try:
            parsed = json.loads(body.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            parsed = None
    return {"http_status": status, "body_sample": body[:200].decode("utf-8", "replace"),
            "json_error_field": (parsed or {}).get("error") if isinstance(parsed, dict) else None,
            # 实测：FastAPI 鉴权中间件先于路由执行 → 配置了 API_KEY 时未知路径也 401，
            # 404 永不可见。两种形态都合法，但必须稳定且与基线一致。
            "not_found_semantics": "401-auth-masks-404" if status == 401 else "plain-404",
            "assert": status in (401, 404)}


@probe("protected_no_credential_reject")
def _p_no_cred(ctx):
    """保护端点拒绝行为：fail-closed 形态必须稳定（401 或 503，记录是哪种）。"""
    status, headers, body, ms = _fetch("POST", ctx["base"] + ctx["sse_path"],
                                       headers={"Content-Type": "application/json"},
                                       body=b"{}")
    parsed = None
    if body[:1] == b"{":
        try:
            parsed = json.loads(body.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            parsed = None
    return {"http_status": status, "body_sample": body[:300].decode("utf-8", "replace"),
            "error_field": (parsed or {}).get("error") if isinstance(parsed, dict) else None,
            "reject_mode": "401" if status == 401 else ("503-fail-closed" if status == 503 else None),
            "assert": status in REJECT_STATUSES}


@probe("protected_wrong_api_key")
def _p_wrong_key(ctx):
    if not ctx.get("api_key"):
        return {"status": "skipped", "reason": "API_KEY 未注入环境，无法构造错误凭据场景"}
    status, headers, body, ms = _fetch("POST", ctx["base"] + "/api/chat/stream",
                                       headers={"Content-Type": "application/json",
                                                "X-API-Key": "baseline-wrong-key-000"},
                                       body=b"{}")
    return {"http_status": status, "body_sample": body[:300].decode("utf-8", "replace"),
            "assert": status == 401}


@probe("forged_identity_header_passthrough_probe")
def _p_forged(ctx):
    """伪造身份头直连探针：只验证现状语义（直连=信任边界内，头会被接受）。
    迁移后经 APISIX 必须呈现不同结果（APISIX 侧剥离；直连上游被网络层禁止）。
    本探针不假设对错，只固化事实供比对。"""
    status, headers, body, ms = _fetch("GET", ctx["base"] + "/health",
                                       headers={"X-User-Id": "baseline-forged-user"})
    return {"http_status": status,
            "note": "直连现状：伪造头进入信任边界内属既定事实；迁移后此路径应不可达",
            "assert": status == 200}


# ── 重量级探针（默认关闭）────────────────────────────────────

@probe("sse_stream_shape")
def _p_sse(ctx):
    """SSE 形状探针：记录 content-type、事件类型序列、首事件延迟、chunk 数。
    会触发真实 LLM 调用，必须 --with-sse 显式开启。"""
    if not ctx.get("with_sse"):
        return {"status": "skipped", "reason": "需 --with-sse 显式开启（触发真实 LLM 调用）"}
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if ctx.get("api_key"):
        headers["X-API-Key"] = ctx["api_key"]
    req = request.Request(ctx["base"] + ctx["sse_path"],
                          data=json.dumps({"question": "基线探针：回复一个字即可",
                                           "user_id": ctx.get("user_id", "")}).encode(),
                          method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    start = time.monotonic()
    events: list[str] = []
    first_event_ms = None
    chunk_count = 0
    try:
        with request.urlopen(req, timeout=ctx["sse_timeout"]) as resp:
            content_type = resp.headers.get("content-type", "")
            for raw in resp:
                chunk_count += 1
                line = raw.decode("utf-8", "replace")
                if line.startswith("event:"):
                    if first_event_ms is None:
                        first_event_ms = round((time.monotonic() - start) * 1000, 1)
                    events.append(line.split(":", 1)[1].strip())
    except error.HTTPError as e:
        return {"http_status": e.code, "body_sample": e.read()[:300].decode("utf-8", "replace"),
                "assert": False, "note": "SSE 建流失败"}
    return {"content_type": content_type, "event_types": events, "first_event_ms": first_event_ms,
            "chunk_count": chunk_count,
            "assert": content_type.startswith("text/event-stream") and bool(events)}


@probe("upload_size_limit")
def _p_upload(ctx):
    """上传限制探针：以超过 RAG_MAX_FILE_SIZE 的稀疏文件实测网关/后端限制。"""
    if not ctx.get("with_upload"):
        return {"status": "skipped", "reason": "需 --with-upload 显式开启（大文件 IO）"}
    import os
    import tempfile
    limit = ctx.get("max_file_size")
    if not limit:
        return {"status": "skipped", "reason": "RAG_MAX_FILE_SIZE 未知，无法构造超限文件"}
    path = os.path.join(tempfile.gettempdir(), "baseline_oversize.bin")
    with open(path, "wb") as f:
        f.seek(limit + 1)
        f.write(b"\0")
    try:
        with open(path, "rb") as f:
            status, headers, body, ms = _fetch(
                "POST", ctx["base"] + ctx["upload_path"],
                headers={"Content-Type": "application/octet-stream",
                         **({"X-API-Key": ctx["api_key"]} if ctx.get("api_key") else {})},
                body=f.read(), timeout=60)
    finally:
        os.remove(path)
    return {"http_status": status, "body_sample": body[:300].decode("utf-8", "replace"),
            "assert": status in (413, 400, 422)}


PROBES = [_p_health, _p_index, _p_openapi, _p_metrics,
          _p_preflight, _p_404, _p_no_cred,
          _p_wrong_key, _p_forged,
          _p_sse, _p_upload]


def run(base: str, args) -> tuple[list[dict], dict]:
    ctx = {"base": base.rstrip("/"), "origin": args.origin,
           "api_key": args.api_key or None, "with_sse": args.with_sse,
           "with_upload": args.with_upload, "sse_timeout": args.sse_timeout,
           "max_file_size": args.max_file_size, "upload_path": args.upload_path,
           "sse_path": args.sse_path, "user_id": args.user_id}
    results: list[dict] = []
    for p in PROBES:
        p(ctx, results)
    meta = {"target": ctx["base"], "generated_at": datetime.now(timezone.utc).isoformat(),
            "probe_count": len(results),
            "errors": sum(1 for r in results if r.get("status") == "error"),
            "failed_asserts": [r["name"] for r in results if r.get("assert") is False],
            "skipped": [r["name"] for r in results if r.get("status") == "skipped"]}
    return results, meta


def compare(results_a: list[dict], results_b: list[dict]) -> list[str]:
    """逐探针比对：状态码、SSE 事件序列、CORS 头形状。返回差异描述列表。"""
    diffs: list[str] = []
    idx_a = {r["name"]: r for r in results_a}
    idx_b = {r["name"]: r for r in results_b}
    for name in sorted(set(idx_a) | set(idx_b)):
        a, b = idx_a.get(name), idx_b.get(name)
        if a is None or b is None:
            diffs.append(f"[{name}] 探针单侧缺失（a={a is not None}, b={b is not None}）")
            continue
        for key in ("http_status", "reject_mode", "allow_origin", "allow_credentials",
                    "content_type"):
            if a.get(key) != b.get(key):
                diffs.append(f"[{name}] {key}: 基线={a.get(key)!r} vs 对比={b.get(key)!r}")
        # SSE 事件类型按集合比（次数/顺序随单次对话内容波动，序列本身记录在 JSON 供人工复核）
        if a.get("event_types") is not None or b.get("event_types") is not None:
            sa, sb = set(a.get("event_types") or []), set(b.get("event_types") or [])
            if sa != sb:
                diffs.append(f"[{name}] event_types 集合差异: 仅基线={sorted(sa - sb)} 仅对比={sorted(sb - sa)}")
        if a.get("status") == "error" or b.get("status") == "error":
            diffs.append(f"[{name}] 至少一侧探针执行出错，需人工检查")
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser(description="网关迁移基线检查（默认只读探针）")
    ap.add_argument("--base", required=True, help="目标入口，如 http://127.0.0.1:8000")
    ap.add_argument("--record", help="录制基线到 JSON 文件")
    ap.add_argument("--compare", help="与指定基线 JSON 比对")
    ap.add_argument("--origin", default="http://localhost:3000", help="CORS 预检用的 Origin")
    ap.add_argument("--api-key", default=None, help="X-API-Key（或经环境变量 API_KEY 提供）")
    ap.add_argument("--user-id", default="", help="SSE 探针的 user_id（legacy 模式用）")
    ap.add_argument("--with-sse", action="store_true", help="开启 SSE 探针（触发真实 LLM 调用）")
    ap.add_argument("--with-upload", action="store_true", help="开启上传超限探针（大文件 IO）")
    ap.add_argument("--sse-timeout", type=float, default=300.0)
    ap.add_argument("--max-file-size", type=int, default=None, help="RAG_MAX_FILE_SIZE 字节数")
    ap.add_argument("--upload-path", default="/api/rag/upload", help="上传端点路径")
    ap.add_argument("--sse-path", default="/api/chat/stream",
                    help="chat 流端点：APISIX 目标用 /api/chat/stream，直连 FastAPI 用 /chat/stream")
    args = ap.parse_args()

    import os
    if not args.api_key:
        args.api_key = os.environ.get("API_KEY") or None

    results, meta = run(args.base, args)
    out = {"meta": meta, "results": results}

    if args.record:
        with open(args.record, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"基线已写入 {args.record}")

    print(json.dumps(meta, ensure_ascii=False, indent=2))
    exit_code = 0
    if meta["errors"]:
        exit_code = 1
        print(f"!! {meta['errors']} 个探针执行出错（见 JSON 的 error 字段）")
    if meta["failed_asserts"]:
        print(f"!! 断言失败：{meta['failed_asserts']}")

    if args.compare:
        with open(args.compare, encoding="utf-8") as f:
            base_doc = json.load(f)
        diffs = compare(base_doc["results"], results)
        if diffs:
            exit_code = 1
            print("!! 与基线存在差异：")
            for d in diffs:
                print("   -", d)
        else:
            print("与基线一致：状态码/CORS/SSE 形状无差异")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
