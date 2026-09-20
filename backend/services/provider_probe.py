"""provider_probe.py — 供应商连通性分级探测（P1b）

按 `docs/model-config-governance-design.md` B.4 / §B.14 实现分级探测，供管理端
供应商 tab 的「测试连接」调用（已存实例复测 + 草稿态测试）。

**探测链只保留两级判死与判定，其余能力一律移出关键路径**（2026-09-21 分层重划）：

| 级 | 动作 | 失败含义 | 在不在「测试连接」里 |
|---|---|---|---|
| L0 | url_guard + TCP/TLS | 地址写错 / 不可达 / 证书异常 | **在**（安全闸门 + 短路） |
| L2 | 最小 chat 调用（快速模式取首个流式分片，完整模式等非流式结果） | 区分「地址协议错」「模型名错」「Key 错」 | **在**（唯一判定依据） |
| L3 | `stream=true` 观察是否回传 usage（完整模式） | 不回传 → `skip`（记账会缺 token 数） | 仅在完整模式 |
| ~~L1~~ | ~~`GET {base}/models`~~ → 已移出，见 `fetch_model_catalog()` | —— | **不在**（改为按需资源） |

### 为什么 L1 不再是一级探测

L1 从来**不参与判定**（它的失败一律降级），它唯一不可替代的价值是**那份模型名清单**。
而这份清单属于「填写阶段」的**输入辅助**，不属于「检测阶段」的**判定结论**。把它挂在
探测链上有两个实际代价：

1. **拖慢成功路径**：它是串行 `await`，且超时上限 8s。大量中转站 / 编码套餐压根不实现
   `/models`，于是用户最坏白等 8 秒，最后只换来一句「已跳过（不影响使用）」。
2. **污染探测响应**：`GET /models` 的原文会被塞进 `ProbeStep.detail`，而 `detail` 受
   `DETAIL_LIMIT` 截断（实测百炼一次返回 **507** 个模型名，远超声明的 200 字上限）——
   清单既传不全，又白占一个每次都要传输的响应字段。

因此改为：**探测 = L0 + L2**（成功路径最短、失败路径也不再被拖），**模型目录 = 一个
独立的按需只读接口**（`fetch_model_catalog`，带短 TTL 缓存）。判定归判定，填料归填料。

### 三条实现约定（都有原因，勿擅改）

1. **只有 L0 失败短路**，其余各级失败只记录、继续往下测（B.4 硬约束 1）。这条在 L1
   移出后依然成立：**判定唯一的依据是 L2**，任何"提前判死"都会让测试按钮失去可信度。
2. **L2/L3 用裸 langchain 客户端调用，不走 `proxy`**：既复用真实客户端栈
   （B.4 硬约束 2），又天然不产生 usage 记录 / 不烧预算 / 不进
   `llm_usage_attribution` —— B.4 硬约束 3 要求的「探测流量排除在统计之外」
   由此**结构性满足**，无需侵入 token_tracker 打标。
3. **给人看的中文进 `summary`，排障用的原文进 `detail`**（2026-09-21 起）。此前把
   `{异常类名}: {原文}` 直接拼在 `summary` 末尾，用户看到的是
   `模型名错误或该 Key 无权访问该模型 —— OpenAIModelNotFoundError: Error code: 404 - {...}`
   —— 结论对，但后半截是给开发者看的。现在 `summary` **只讲人话**，任何异常类名 /
   JSON / 厂商英文报文一律进 `detail`（仍受 `DETAIL_LIMIT` 截断，B.4 硬约束 4 的
   「排障第一现场」不丢，只是换了字段）。

安全边界（B.6）：私网放行**只**由调用方按 `network_scope == "private"` 显式
传入，绝不可由「解析出来是私网」自动推导 —— 否则 DNS rebinding 直接绕过。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import ssl
import time
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlparse

from backend.services import specialized_model_probe
from backend.shared.logger import logger
from backend.tools.url_guard import UrlBlockedError, assert_url_allowed

DETAIL_LIMIT = 200

_L0_TIMEOUT = 5.0
_L2_FAST_TIMEOUT = 10.0
_L2_TIMEOUT = 20.0

# 模型目录（原 L1 的能力，已移出探测链）—— 按需请求，用户点了才付这份等待。
_CATALOG_TIMEOUT = 8.0
_CATALOG_LIMIT = 2000          # 单次返回条数上限，超出置 truncated
_CATALOG_CACHE_TTL = 300.0     # 同一端点 + 同一密钥 5 分钟内复用
_CATALOG_CACHE_MAX = 64        # 进程内缓存条目上限（防无界增长）

_PROBE_PROMPT = "ping"
_PROBE_MAX_TOKENS = 16
_PROBE_FAST_MAX_TOKENS = 1
_TP_MODEL_SUFFIX = "@tp"

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_DEGRADED = "fail_degraded"
STATUS_SKIP = "skip"

_MODEL_KINDS = frozenset({"chat", "embedding", "rerank", "vision", "speech"})

# 归因代号：前端据此决定显示哪个「去修」动作（不要在文案里做字符串匹配）。
# 只是**显示层**契约，不影响 ok / blocked_at 的判定语义。
REASON_BLOCKED = "blocked"            # 被安全策略拦截（私网 / 云元数据）
REASON_INVALID_URL = "invalid_url"    # URL 本身不合法（缺 host 等）
REASON_UNREACHABLE = "unreachable"    # DNS / TCP / TLS 连不上
REASON_TIMEOUT = "timeout"            # 连接或调用超时
REASON_BASE_URL = "base_url"          # 地址不是 OpenAI 兼容基址（路由不存在）
REASON_MODEL_NAME = "model_name"      # 模型名错 / 该 Key 无权访问该模型
REASON_API_KEY = "api_key"            # Key 无效或无权
REASON_CLIENT_BUILD = "client_build"  # 客户端初始化失败（协议与地址不匹配）
REASON_NO_MODEL = "no_model"          # 未提供模型名

# 失败归因的文本线索（顺序有意义：先判更具体的「模型名」再判「Key」）
_MODEL_ERROR_HINTS = (
    "model not found", "does not exist", "unknown model", "no such model",
    "invalid model", "model_not_found", "not a valid model",
    # CamelCase 异常类名拼成的一整串（如 langchain_openai 的
    # `OpenAIModelNotFoundError`），没有空格也没有下划线，上面几条都匹配不到。
    "modelnotfound",
)
_KEY_ERROR_HINTS = (
    "invalid api key", "incorrect api key", "invalid_api_key", "unauthorized",
    "authentication", "api key", "401", "403",
)


# ── 结果结构 ────────────────────────────────────────────────────────────


@dataclass
class ProbeStep:
    """单级探测结果。`status` ∈ pass / fail / fail_degraded / skip。

    - `summary`：**只给人看的中文结论**，不得出现异常类名 / JSON / 英文报文。
    - `detail`：排障用原文（英文异常、上游响应体、返回内容），受 `DETAIL_LIMIT` 截断。
    - `reason`：归因代号（`REASON_*`），供前端选择「去修」动作；非失败步骤为 None。
    """

    level: str
    status: str
    summary: str
    detail: str = ""
    elapsed_ms: int = 0
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "status": self.status,
            "summary": self.summary,
            "detail": self.detail,
            "elapsed_ms": self.elapsed_ms,
            "reason": self.reason,
        }


@dataclass
class ProbeResult:
    """四级探测总结果。`blocked_at` 为**首个**判死级别（None = 全通过）。"""

    ok: bool
    blocked_at: str | None
    steps: list[ProbeStep] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "blocked_at": self.blocked_at,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
        }


def _clip(text: Any, limit: int = DETAIL_LIMIT) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[:limit] + "…（已截断）"


def _step(
    level: str, status: str, summary: str, *, detail: Any = "", t0: float | None = None,
    reason: str | None = None,
) -> ProbeStep:
    ms = int((time.monotonic() - t0) * 1000) if t0 is not None else 0
    return ProbeStep(level=level, status=status, summary=summary,
                     detail=_clip(detail), elapsed_ms=ms, reason=reason)


# ── L0：URL + DNS + TCP/TLS ─────────────────────────────────────────────


def _tcp_tls_ok(host: str, port: int, scheme: str, timeout: float) -> None:
    """建连一次；https 再握一次 TLS（证书问题会在此暴露）。"""
    with socket.create_connection((host, port), timeout=timeout):
        pass
    if scheme == "https":
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host):
                pass


async def probe_l0(base_url: str, *, allow_private: bool) -> ProbeStep:
    t0 = time.monotonic()
    try:
        assert_url_allowed(base_url, allow_private=allow_private)
    except UrlBlockedError as e:
        return _step("L0", STATUS_FAIL, f"地址被安全策略拦截：{e}", t0=t0,
                     reason=REASON_BLOCKED)

    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    if not host:
        return _step("L0", STATUS_FAIL,
                     "地址不完整：缺少主机名（形如 https://api.example.com/v1）",
                     t0=t0, reason=REASON_INVALID_URL)
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)

    try:
        await asyncio.wait_for(
            asyncio.to_thread(_tcp_tls_ok, host, port, parsed.scheme.lower(), _L0_TIMEOUT),
            timeout=_L0_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return _step("L0", STATUS_FAIL,
                     f"连接 {host}:{port} 超时 —— 请检查地址、端口与网络是否可达",
                     t0=t0, reason=REASON_TIMEOUT)
    except Exception as e:  # noqa: BLE001 — 探测必须把任何异常转成结论
        # 异常类名（ConnectionRefusedError / SSLCertVerificationError …）只进 detail：
        # 它是有用的排障线索，但不是给用户看的结论。
        return _step("L0", STATUS_FAIL,
                     f"不可达（{host}:{port}）—— 请检查地址、端口、网络与证书",
                     detail=f"{type(e).__name__}: {e}", t0=t0,
                     reason=REASON_UNREACHABLE)

    return _step("L0", STATUS_PASS, f"URL 可达（{host}:{port}，TLS 正常）", t0=t0)


# ── 模型目录：GET {base}/models（按需资源，不在探测链上）────────────────
#
# 这是原 L1 的能力，2026-09-21 从探测链移出（原因见模块头）。它唯一的产出是
# 「这个端点上有哪些模型名可以填」——属于**填写阶段的输入辅助**，不是判定结论。
# 它**不参与任何 ok / blocked_at 判定**，失败也只影响「能不能帮你省掉手打」。


def _models_body_is_openai_shaped(body: str) -> bool | None:
    """`/models` 响应体是否为 OpenAI 形状：``{"object":"list","data":[...]}``。

    返回 ``True`` / ``False`` / ``None``（判断不了 → 保持宽容，不误报）。

    这条嗅探专门用来抓「**地址填成了厂商原生协议端点**」：阿里云百炼的原生
    `/api/v1/models` 返回 ``{"success":true,"output":{"models":[...]}}``，**同样是
    200**，能读出模型名；但 OpenAI 协议要打的 `/chat/completions` 在那个前缀下并不
    存在（实测 404 且 body 为空），L2 必然失败 —— 用户只会看到一个 404，然后去猜
    模型名。所以：**清单照样给用户抄，但地址问题必须当场点破**。
    """
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("data"), list):
        return True
    # 明确的「厂商原生信封」特征（百炼原生：code / message / success / output）
    if {"output", "success", "code"} & data.keys():
        return False
    return None


def _extract_catalog_ids(data: Any) -> tuple[list[str], int | None]:
    """从响应体里抽出模型名，兼容 OpenAI 与「厂商原生信封」两种形状。

    返回 ``(模型名列表, 厂商声称的总数或 None)``。顺序即厂商返回顺序，**不去重也不排序**
    —— 排序与过滤是展示层的事，这里保持原样便于排障时对照上游。
    """
    if not isinstance(data, dict):
        return [], None

    # OpenAI 兼容：{"object":"list","data":[{"id":"..."}]}
    raw = data.get("data")
    if isinstance(raw, list):
        out = [
            str(item.get("id") or item.get("name"))
            for item in raw
            if isinstance(item, dict) and (item.get("id") or item.get("name"))
        ]
        return out, None

    # 厂商原生信封：{"output":{"models":[{"model":"..."}],"total":507}}
    output = data.get("output")
    if isinstance(output, dict):
        raw = output.get("models")
        if isinstance(raw, list):
            out = [
                str(item.get("model") or item.get("id") or item.get("name"))
                for item in raw
                if isinstance(item, dict)
                and (item.get("model") or item.get("id") or item.get("name"))
            ]
            total = output.get("total")
            return out, total if isinstance(total, int) else None

    return [], None


# 名称启发式分用途。**只是展示分组**（默认只展开「对话」组），不参与任何判定，
# 也永远不会因为分错而拒绝某个模型名 —— 用户可以展开全部或直接手打。
_KIND_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rerank", ("rerank", "reranker")),
    ("embedding", ("embedding", "text-embedding", "bge", "gte-", "embed-", "-embed")),
    ("speech", ("tts", "asr", "speech", "audio", "voice", "whisper", "cosyvoice")),
    ("vision", ("-vl", "vl-", "vision", "ocr", "omni", "gpt-4o", "qvq")),
)


def _classify_model_kind(model_id: str) -> str:
    low = (model_id or "").lower()
    for kind, needles in _KIND_RULES:
        if any(n in low for n in needles):
            return kind
    return "chat"


@dataclass
class ModelCatalogItem:
    id: str
    kind: str = "chat"

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind}


@dataclass
class ModelCatalog:
    """模型目录结果。`ok` = 这次有没有拿到可用清单；`shape_ok` = 地址看着对不对。"""

    ok: bool
    status: str
    summary: str
    reason: str | None = None
    items: list[ModelCatalogItem] = field(default_factory=list)
    detail: str = ""
    truncated: bool = False
    total: int | None = None
    shape_ok: bool | None = None
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "summary": self.summary,
            "reason": self.reason,
            "items": [i.to_dict() for i in self.items],
            "count": len(self.items),
            "total": self.total,
            "truncated": self.truncated,
            "shape_ok": self.shape_ok,
            "cached": self.cached,
        }


# 进程内短 TTL 缓存：同一「地址 + 密钥」在 TTL 内复用，避免反复点就反复拉。
# 键里只放密钥摘要（sha256 前 16 位），**绝不缓存密钥本身**。
_catalog_cache: dict[str, tuple[float, ModelCatalog]] = {}


def _key_digest(api_key: str) -> str:
    if not api_key:
        return "-"
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def reset_catalog_cache_for_tests() -> None:
    """测试态：清空目录缓存。"""
    _catalog_cache.clear()


def _catalog_cache_get(key: str) -> ModelCatalog | None:
    hit = _catalog_cache.get(key)
    if hit is None:
        return None
    ts, value = hit
    if time.monotonic() - ts > _CATALOG_CACHE_TTL:
        _catalog_cache.pop(key, None)
        return None
    # 返回独立副本并标记 cached：调用方拿到的东西不会被后续缓存写入串改。
    return replace(value, cached=True)


def _catalog_cache_put(key: str, value: ModelCatalog) -> None:
    if key not in _catalog_cache and len(_catalog_cache) >= _CATALOG_CACHE_MAX:
        oldest = next(iter(_catalog_cache), None)  # dict 保序 → 最早插入的那条
        if oldest is not None:
            _catalog_cache.pop(oldest, None)
    _catalog_cache[key] = (time.monotonic(), value)


async def fetch_model_catalog(
    base_url: str,
    api_key: str = "",
    *,
    extra_headers: dict[str, str] | None = None,
    allow_private: bool = False,
    use_cache: bool = True,
) -> ModelCatalog:
    """按需拉取 `GET {base}/models`，产出「可填的模型名」清单。

    与探测链的两点区别（这也是它被移出探测的原因）：

    1. **失败不判死**：拿不到清单只意味着用户要手打模型名，不代表供应商不可用。
       因此这里永远不产生 `blocked_at`，`status` 只描述「取清单」这件事本身。
    2. **只回报名称**：原始响应只进 `detail` 供排障，绝不再被当成探测结论的一部分。

    安全：与探测一致，出站前先过 `url_guard`（这是唯一拦 SSRF / 内网探测的地方），
    私网放行只能由调用方按 `network_scope == "private"` 显式传入。
    """
    t0 = time.monotonic()

    try:
        assert_url_allowed(base_url, allow_private=allow_private)
    except UrlBlockedError as e:
        return ModelCatalog(ok=False, status=STATUS_FAIL, reason=REASON_BLOCKED,
                            summary=f"地址被安全策略拦截：{e}")

    cache_key = f"{base_url.rstrip('/')}|{_key_digest(api_key)}"
    if use_cache:
        hit = _catalog_cache_get(cache_key)
        if hit is not None:
            return hit

    url = base_url.rstrip("/") + "/models"
    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
    if extra_headers:
        headers.update(extra_headers)

    try:
        import httpx

        async with httpx.AsyncClient(timeout=_CATALOG_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
    except Exception as e:  # noqa: BLE001
        return ModelCatalog(
            ok=False, status=STATUS_FAIL, reason=REASON_UNREACHABLE,
            summary="取不到模型清单：端点没有响应 —— 请检查地址、网络与证书",
            detail=f"{type(e).__name__}: {e}",
        )

    body = resp.text
    if resp.status_code in (401, 403):
        return ModelCatalog(
            ok=False, status=STATUS_FAIL, reason=REASON_API_KEY,
            summary=f"模型清单取不到：Key 被拒绝（HTTP {resp.status_code}）—— 请检查 API Key",
            detail=body,
        )
    if resp.status_code == 404:
        missing_v1 = "/v1" not in url
        return ModelCatalog(
            ok=False, status=STATUS_DEGRADED, reason=REASON_BASE_URL,
            summary=(
                "该站点没有模型清单接口"
                + ("（地址可能少了 /v1）" if missing_v1 else "")
                + " —— 可直接手动填写模型名，不影响使用"
            ),
            detail=body,
        )
    if resp.status_code != 200:
        return ModelCatalog(
            ok=False, status=STATUS_DEGRADED,
            summary=f"模型清单取不到（HTTP {resp.status_code}）—— 可直接手动填写模型名",
            detail=body,
        )

    try:
        parsed = json.loads(body)
    except Exception:  # noqa: BLE001
        parsed = None

    shape_ok = _models_body_is_openai_shaped(body)
    ids, total = _extract_catalog_ids(parsed)
    items = [ModelCatalogItem(id=i, kind=_classify_model_kind(i)) for i in ids]
    truncated = len(items) > _CATALOG_LIMIT
    if truncated:
        items = items[:_CATALOG_LIMIT]

    if shape_ok is False:
        # 能读到名字，但信封是厂商原生格式 → 地址大概率不是 OpenAI 兼容基址。
        # 清单照给（用户可先抄名字），但必须当场点破，否则又是「L2 404 却去猜模型名」。
        summary = (
            f"读到 {len(items)} 个模型名，但这个地址返回的不是 OpenAI 兼容格式"
            " —— 对话调用大概率 404，建议给地址补 /compatible-mode 或 /v1"
            if items else
            "该地址返回的不是 OpenAI 兼容格式 —— 对话调用大概率 404，"
            "建议给地址补 /compatible-mode 或 /v1"
        )
        result = ModelCatalog(
            ok=bool(items), status=STATUS_DEGRADED, reason=REASON_BASE_URL,
            summary=summary, items=items, detail=body, truncated=truncated,
            total=total, shape_ok=False,
        )
    elif not items:
        result = ModelCatalog(
            ok=False, status=STATUS_DEGRADED,
            summary="端点已响应，但没有解析出模型名 —— 可直接手动填写",
            detail=body, shape_ok=shape_ok,
        )
    else:
        suffix = "（超出上限已截断）" if truncated else ""
        result = ModelCatalog(
            ok=True, status=STATUS_PASS,
            summary=f"共 {len(items)} 个模型可选{suffix}",
            items=items, detail=body, truncated=truncated, total=total,
            shape_ok=shape_ok,
        )

    if use_cache and result.ok:
        _catalog_cache_put(cache_key, result)
    return result


# ── L2：最小 chat 调用（真实客户端栈） ──────────────────────────────────


def build_probe_client(
    driver: str,
    *,
    model_name: str,
    api_key: str,
    base_url: str,
    extra_headers: dict[str, str] | None = None,
    fast: bool = False,
) -> Any:
    """按 **driver**（协议）构建最小客户端。

    草稿态探测没有 provider id，模型可能也未注册，故只能按 driver 构建 ——
    这正是 B.1「厂商差异不在代码里」的直接推论。内置 provider 的 `build_*`
    与之使用同一组 langchain 客户端与参数（P1a-1 已统一为传参式），完整探测固定
    `max_tokens=16` 与 `temperature=0`；快速探测将输出上限降为 1，并按需取首片。

    各客户端**延迟导入**（照 providers/*.py 约定），避免把重依赖拖进导入链。
    """
    d = (driver or "").strip().lower()

    max_tokens = _PROBE_FAST_MAX_TOKENS if fast else _PROBE_MAX_TOKENS
    client_timeout = _L2_FAST_TIMEOUT if fast else _L2_TIMEOUT

    if d == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model_name, base_url=base_url,
            temperature=0.0, num_predict=max_tokens,
        )

    if d == "anthropic":
        from langchain_anthropic import ChatAnthropic

        headers = {"x-api-key": api_key}
        headers.update(extra_headers or {})
        return ChatAnthropic(
            model=model_name,
            anthropic_api_key=api_key,
            anthropic_api_url=base_url,
            default_headers=headers,
            max_tokens=max_tokens,
            timeout=client_timeout,
        )

    if d in ("openai", ""):
        from langchain_openai import ChatOpenAI

        kwargs: dict[str, Any] = {
            # qwen_tp 的 @tp 只用于内部注册名，Token Plan API 只接受真实模型名。
            "model": model_name.removesuffix(_TP_MODEL_SUFFIX),
            "api_key": api_key,
            "base_url": base_url,
            "default_headers": dict(extra_headers) if extra_headers else None,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "request_timeout": client_timeout,
        }
        parsed_host = (urlparse(base_url).hostname or "").lower()
        if (
            fast
            and parsed_host.endswith("siliconflow.cn")
            and "qwen3" in model_name.lower()
            and "thinking" not in model_name.lower()
        ):
            # SiliconFlow 的 Qwen3 默认可能进入思考模式；快速探测只验证
            # 鉴权与模型可调用，不应等待完整思考链。该参数仅发给明确匹配的
            # SiliconFlow Qwen3，避免污染其他 OpenAI 兼容供应商的请求。
            kwargs["extra_body"] = {"enable_thinking": False}
        return ChatOpenAI(**kwargs)

    raise ValueError(f"不支持的驱动: {driver or '(空)'}（可选 openai / anthropic / ollama）")


def _invoke_once(client: Any) -> str:
    from langchain_core.messages import HumanMessage

    resp = client.invoke([HumanMessage(content=_PROBE_PROMPT)])
    content = getattr(resp, "content", resp)
    return content if isinstance(content, str) else str(content)


def _stream_first_chunk(client: Any) -> str:
    """只取首个流式分片，并主动关闭迭代器，避免快速测试等待完整生成。"""
    from langchain_core.messages import HumanMessage

    stream = client.stream([HumanMessage(content=_PROBE_PROMPT)])
    try:
        chunk = next(iter(stream))
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()

    content = getattr(chunk, "content", chunk)
    return content if isinstance(content, str) else str(content)


@dataclass
class FailureAttribution:
    """一次失败的结构化归因：结论（中文）/ 代号 / 排障原文。

    三者分开是刻意的 —— 见模块头第 3 条：**给用户看的和给排障看的不该挤在同一句里**。
    """

    summary: str
    reason: str
    raw: str


def _classify_l2_failure(exc: Exception, api_key: str = "") -> FailureAttribution:
    """把 L2 异常归因到「地址不是 OpenAI 兼容基址」/「模型名错」/「Key 错」。

    归因顺序有讲究（探测结果是排障第一现场，B.4 硬约束 4）：

    1. **先判「地址错」，再判「模型名错」**。上游对不存在的路径常直接甩一个
       **空 body 的 404**（甚至发生在鉴权之前）；而 langchain_openai 会把**任何**
       404 一律包装成 `OpenAIModelNotFoundError`，类名里的 "ModelNotFound" 极易
       把人骗去改模型名。实测的典型坑：把某厂商的**原生协议地址**当 OpenAI 兼容
       基址填（例如百炼的 `/api/v1` vs `/compatible-mode/v1`）—— 此时换任何模型名
       都无效。**空 body 正是区分二者的关键**：真正的「模型不存在」上游会回一段
       结构化 JSON（含 error code / message）。
    2. 其余 404（带 body）仍归「模型名错」。

    英文异常原文（`OpenAIModelNotFoundError: Error code: 404` 之类）**只进 `raw`**，
    由调用方放进 `ProbeStep.detail`，绝不拼进 `summary`。
    """
    raw = f"{type(exc).__name__}: {exc}"
    if api_key:
        raw = raw.replace(api_key, "<API_KEY>")
    low = raw.lower()
    status = getattr(exc, "status_code", None)

    if status == 404 and "{" not in raw:
        return FailureAttribution(
            summary=(
                "接口地址可能填错了：这个地址下没有对话接口（HTTP 404，且响应体为空）。"
                "请检查地址是否漏了 /v1 或 /compatible-mode/v1"
            ),
            reason=REASON_BASE_URL,
            raw=raw,
        )
    if any(h in low for h in _MODEL_ERROR_HINTS):
        return FailureAttribution(
            summary=(
                "模型名不对，或这个 Key 没有权限访问该模型 —— "
                "可以从「模型清单」里选一个，或确认该模型确实存在"
            ),
            reason=REASON_MODEL_NAME,
            raw=raw,
        )
    if any(h in low for h in _KEY_ERROR_HINTS):
        return FailureAttribution(
            summary="API Key 无效或没有权限 —— 请重新粘贴 Key（注意别带多余空格）",
            reason=REASON_API_KEY,
            raw=raw,
        )
    if status == 404:
        return FailureAttribution(
            summary=(
                "模型名不对，或这个 Key 没有权限访问该模型 —— "
                "可以从「模型清单」里选一个，或确认该模型确实存在"
            ),
            reason=REASON_MODEL_NAME,
            raw=raw,
        )
    return FailureAttribution(
        summary="最小调用失败：没能完成一次对话 —— 请检查地址、Key 与模型名",
        reason="unknown",
        raw=raw,
    )


async def probe_l2(
    driver: str,
    *,
    model_name: str,
    api_key: str,
    base_url: str,
    extra_headers: dict[str, str] | None = None,
    fast: bool = False,
) -> ProbeStep:
    t0 = time.monotonic()
    if not model_name:
        return _step("L2", STATUS_FAIL,
                     "还没填模型名，无法验证模型是否可用", t0=t0,
                     reason=REASON_NO_MODEL)

    try:
        client = build_probe_client(
            driver, model_name=model_name, api_key=api_key,
            base_url=base_url, extra_headers=extra_headers, fast=fast,
        )
    except Exception as e:  # noqa: BLE001
        return _step("L2", STATUS_FAIL,
                     f"无法按「{driver or '默认'}」协议初始化客户端 —— "
                     "请确认所选协议与地址是否匹配",
                     detail=f"{type(e).__name__}: {e}", t0=t0,
                     reason=REASON_CLIENT_BUILD)

    timeout = _L2_FAST_TIMEOUT if fast else _L2_TIMEOUT
    invoke = _stream_first_chunk if fast else _invoke_once
    try:
        content = await asyncio.wait_for(asyncio.to_thread(invoke, client),
                                         timeout=timeout)
    except asyncio.TimeoutError:
        return _step("L2", STATUS_FAIL,
                     f"调用超时（{int(timeout)} 秒内没有响应）—— "
                     "模型可能不可用，或该地址响应过慢",
                     t0=t0, reason=REASON_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        failure = _classify_l2_failure(e, api_key)
        return _step("L2", STATUS_FAIL, failure.summary,
                     detail=failure.raw, t0=t0, reason=failure.reason)

    summary = (
        "模型名可用（已收到首个流式分片）"
        if fast else "模型名可用（已用真实客户端调用）"
    )
    return _step("L2", STATUS_PASS, summary,
                 detail=f"返回：{content}", t0=t0)


# ── L3：流式 usage 回传 ─────────────────────────────────────────────────


def _stream_usage_seen(client: Any) -> tuple[bool, str]:
    from langchain_core.messages import HumanMessage

    seen = False
    text = ""
    for chunk in client.stream([HumanMessage(content=_PROBE_PROMPT)]):
        if getattr(chunk, "usage_metadata", None):
            seen = True
        piece = getattr(chunk, "content", "")
        if isinstance(piece, str):
            text += piece
    return seen, text


async def probe_l3(
    driver: str,
    *,
    model_name: str,
    api_key: str,
    base_url: str,
    extra_headers: dict[str, str] | None = None,
) -> ProbeStep:
    """不判死：只用来决定「记账是否可能缺 token 数」，失败一律 skip。"""
    t0 = time.monotonic()
    try:
        client = build_probe_client(
            driver, model_name=model_name, api_key=api_key,
            base_url=base_url, extra_headers=extra_headers,
        )
        seen, text = await asyncio.wait_for(
            asyncio.to_thread(_stream_usage_seen, client), timeout=_L2_TIMEOUT
        )
    except Exception as e:  # noqa: BLE001
        return _step("L3", STATUS_SKIP,
                     "流式探测未能完成 —— 不影响模型可用性判断",
                     detail=f"{type(e).__name__}: {e}", t0=t0)

    if seen:
        return _step("L3", STATUS_PASS, "流式响应回传 usage（记账完整）",
                     detail=text, t0=t0)
    return _step("L3", STATUS_SKIP,
                 "流式不回传 usage —— 记账会缺 token 数（不影响可用性）",
                 detail=text, t0=t0)


async def _probe_specialized_kind(
    *,
    driver: str,
    base_url: str,
    api_key: str,
    model_name: str,
    model_kind: str,
    network_scope: str,
) -> ProbeResult:
    """用专项适配器测试 embedding/rerank，并转换为统一探测结果。"""
    if driver.strip().lower() not in {"openai", "specialized"}:
        step = ProbeStep(
            level="L2",
            status=STATUS_FAIL,
            summary=f"{model_kind} 模型测试仅支持 OpenAI 兼容协议",
            detail="请将协议设置为 openai；历史专项供应商也会复用同一套探测",
        )
        return ProbeResult(
            ok=False,
            blocked_at="L2",
            steps=[step],
            summary=f"未通过（卡在 L2）：{step.summary}",
        )

    from backend.services.specialized_model_adapters import infer_adapter

    adapter = infer_adapter(model_kind, base_url)
    specialized = await specialized_model_probe.probe_specialized(
        role=model_kind,
        adapter=adapter,
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        network_scope=network_scope,
    )
    status = STATUS_PASS if specialized.ok else STATUS_FAIL
    step = ProbeStep(
        level="L2",
        status=status,
        summary=specialized.summary,
        detail=specialized.detail,
        elapsed_ms=specialized.elapsed_ms,
    )
    if not specialized.ok:
        return ProbeResult(
            ok=False,
            blocked_at="L2",
            steps=[step],
            summary=f"未通过（卡在 L2）：{specialized.summary}：{specialized.detail}",
        )
    return ProbeResult(
        ok=True,
        blocked_at=None,
        steps=[step],
        summary=f"{model_kind} 模型连通性通过",
    )


# ── 入口 ────────────────────────────────────────────────────────────────


async def probe_provider(
    *,
    driver: str,
    base_url: str,
    api_key: str,
    model_name: str,
    network_scope: str = "public",
    extra_headers: dict[str, str] | None = None,
    include_stream_usage: bool = False,
    model_kind: str = "chat",
) -> ProbeResult:
    """执行快速或完整探测。

    **只有 L0 失败短路**（地址不通时后续无意义），**判定完全由 L2 给出**。原 L1
    （`GET /models`）已于 2026-09-21 移出本链路，改为按需的 `fetch_model_catalog()`
    —— 它从不参与判定，只负责「填模型名时给个可选清单」，详见模块头。

    L3 只在完整模式执行，且不参与「模型可调用」的判定。
    """
    kind = (model_kind or "chat").strip().lower()
    if kind not in _MODEL_KINDS:
        step = ProbeStep(
            level="L2",
            status=STATUS_FAIL,
            summary=f"不支持的模型用途：{model_kind}",
            detail="可选用途：chat、embedding、rerank、vision、speech",
        )
        return ProbeResult(
            ok=False,
            blocked_at="L2",
            steps=[step],
            summary=f"未通过（卡在 L2）：{step.summary}",
        )

    if kind in {"embedding", "rerank"}:
        return await _probe_specialized_kind(
            driver=driver,
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            model_kind=kind,
            network_scope=network_scope,
        )

    allow_private = (network_scope or "public").strip().lower() == "private"
    steps: list[ProbeStep] = []

    l0 = await probe_l0(base_url, allow_private=allow_private)
    steps.append(l0)
    if l0.status == STATUS_FAIL:
        return ProbeResult(ok=False, blocked_at="L0", steps=steps,
                           summary=f"未通过（卡在 L0）：{l0.summary}")

    l2 = await probe_l2(
        driver, model_name=model_name, api_key=api_key, base_url=base_url,
        extra_headers=extra_headers, fast=not include_stream_usage,
    )
    steps.append(l2)

    if l2.status != STATUS_PASS:
        logger.info(
            "[ProviderProbe] 探测未通过：driver=%s target=%s scope=%s blocked=L2",
            driver, base_url, "private" if allow_private else "public",
        )
        return ProbeResult(ok=False, blocked_at="L2", steps=steps,
                           summary=f"未通过（卡在 L2）：{l2.summary}")

    if not include_stream_usage:
        steps.append(_step(
            "L3", STATUS_SKIP,
            "快速测试已跳过流式 usage 检查（不影响模型可用性）",
            detail="如需核对流式 usage，请执行完整测试",
        ))
        return ProbeResult(
            ok=True,
            blocked_at=None,
            steps=steps,
            summary="模型连通性通过（快速测试）",
        )

    l3 = await probe_l3(driver, model_name=model_name, api_key=api_key,
                        base_url=base_url, extra_headers=extra_headers)
    steps.append(l3)

    logger.info(
        "[ProviderProbe] 探测通过：driver=%s target=%s scope=%s 耗时=%dms",
        driver, base_url, "private" if allow_private else "public",
        sum(s.elapsed_ms for s in steps),
    )
    return ProbeResult(ok=True, blocked_at=None, steps=steps,
                       summary="厂商连通性通过")
