"""mcp_registry.store — MCP 动态工具注册表的进程内快照（唯一运行时视图）。

数据流（ADR-0011 D2）：
  ai.mcp_* 表（事实源）→ load_snapshot() 派生不可变快照 → 本模块进程内
  缓存 → tool_governance/registry 与 router/manifest 合并消费。

一致性契约：
  - 每 worker 15s 轮询；管理端写操作后调用 refresh_now() 本进程立即生效，
    其余 worker 最迟一个周期收敛；
  - 刷新失败 fail-open：保留上次已知快照，绝不因 DB 抖动清空已生效工具；
  - 快照只含 `approved + 未漂移 + 连接 enabled` 的工具——未审核、Schema
    变化、连接停用三者在派生层即被排除，运行时无需再判断。

签名变化时触发失效回调（capability_registry 缓存、向量路由索引对账），
回调软失败不阻断刷新。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.shared.logger import logger

_REFRESH_INTERVAL_S = 15.0
_CAPABILITY_PREFIX = "mcp."


@dataclass(frozen=True)
class McpConnectionRuntime:
    connection_id: str
    base_url: str
    auth_type: str
    network_scope: str
    secret_cipher: str = ""  # '' = 无凭据；密文仅在调用点解密，不进日志


@dataclass(frozen=True)
class McpToolRuntime:
    capability: str
    tool_id: int
    connection_id: str
    upstream_tool_name: str
    tenant_id: str
    spec: Any  # ToolSpec（避免与 specs.py 循环引用，运行时必为 ToolSpec）
    network_scope: str = "public"
    # 派生输入（描述/Schema/治理配置/示例）的稳定摘要——签名比较用，
    # 同名工具内容变化时让其他 worker 的派生缓存失效
    content_digest: str = ""


@dataclass(frozen=True)
class McpRegistrySnapshot:
    loaded: bool = False
    tools: dict[str, McpToolRuntime] = field(default_factory=dict)
    connections: dict[str, McpConnectionRuntime] = field(default_factory=dict)
    # capability → CapabilityDecl（路由面合并视图的数据源）
    capability_decls: dict[str, Any] = field(default_factory=dict)
    # (product_key, capability, tenant_id) 三元组：产品授权的执行契约视图
    grants: frozenset[tuple[str, str, str]] = frozenset()
    # 派生失败的工具（capability, 原因）——不进快照，管理端可见
    errors: tuple[tuple[str, str], ...] = ()
    refreshed_at: float = 0.0

    def signature(self) -> str:
        payload = (
            sorted(self.tools.keys()),
            sorted(self.connections.keys()),
            sorted(self.grants),
            self.errors,
            tuple(sorted(self.capability_decls.keys())),
            # 声明内容参与签名：只比键集合时，同名工具的描述/Schema/
            # 治理配置变化不会触发其他 worker 失效派生缓存（CAPABILITY_
            # SCHEMA 停留旧内容），必须按内容摘要比较。
            tuple(sorted(
                (cap, tool.content_digest)
                for cap, tool in self.tools.items()
            )),
        )
        return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()[:16]


_SELECT_CONNECTIONS = """
SELECT c.id, c.base_url, c.auth_type, c.network_scope, k.secret_cipher
FROM ai.mcp_connections c
LEFT JOIN ai.mcp_connection_credentials k ON k.connection_id = c.id
WHERE c.enabled
"""

_SELECT_TOOLS = """
SELECT t.id, t.connection_id, t.tool_name, t.capability_slug,
       t.description, t.governance, t.examples, t.tenant_id,
       s.input_schema, s.output_schema
FROM ai.mcp_tools t
LEFT JOIN LATERAL (
    SELECT input_schema, output_schema
    FROM ai.mcp_tool_snapshots
    WHERE tool_id = t.id
    ORDER BY discovery_version DESC, id DESC LIMIT 1
) s ON true
WHERE t.status = 'approved' AND t.schema_changed = false
"""

_SELECT_GRANTS = """
SELECT g.tool_id, g.product_key, g.tenant_id
FROM ai.mcp_tool_grants g
JOIN ai.mcp_tools t ON t.id = g.tool_id
WHERE g.enabled AND g.revoked_at IS NULL AND t.status = 'approved'
"""

# 快照查询结果缓存（DB 行），供管理端"当前生效视图"接口复用
_last_rows: dict[str, list[dict]] = {}

_lock = threading.Lock()
_snapshot: McpRegistrySnapshot = McpRegistrySnapshot()
_last_signature: str = ""
_change_callbacks: list[Callable[[], None]] = []


def snapshot() -> McpRegistrySnapshot:
    """当前进程内快照（线程安全，永不为 None）。"""
    with _lock:
        return _snapshot


def last_rows() -> dict[str, list[dict]]:
    """最近一次刷新的原始行（管理端生效视图诊断用）。"""
    return dict(_last_rows)


def register_on_change(callback: Callable[[], None]) -> Callable[[], None]:
    """注册快照变化回调；返回注销函数。回调异常软失败。"""
    _change_callbacks.append(callback)

    def _unregister() -> None:
        try:
            _change_callbacks.remove(callback)
        except ValueError:
            pass

    return _unregister


def _invalidate_consumers() -> None:
    """快照签名变化 → 失效派生缓存（capability_registry / 向量索引对账等）。"""
    for callback in list(_change_callbacks):
        try:
            callback()
        except Exception:  # noqa: BLE001 — 失效钩子软失败，不阻断刷新
            logger.warning("[McpRegistry] 失效回调执行失败", exc_info=True)


def _static_capability_conflicts(capabilities: list[str]) -> set[str]:
    """与静态注册表重名的动态 capability（防御性；mcp. 前缀正常不冲突）。"""
    try:
        from backend.core.tool_governance.registry import static_tool_specs

        return set(capabilities).intersection(static_tool_specs().keys())
    except Exception:  # noqa: BLE001 — 静态表加载失败按无冲突处理
        return set()


def derived_content_digest(
    description, input_schema, governance, examples,
) -> str:
    """派生输入内容的稳定摘要（描述/Schema/治理/示例）——快照签名比较用。

    与 spec_lib.content_hash（发现内容，绑定审核）语义不同：本摘要覆盖
    审核后的治理字段，用于让"同名工具内容变化"在其他 worker 触发失效。
    """
    payload = json.dumps(
        {
            "description": str(description or ""),
            "input_schema": input_schema or {},
            "governance": governance or {},
            "examples": examples or [],
        },
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def load_snapshot() -> McpRegistrySnapshot:
    """读 ai.mcp_* 三段并派生快照。DB 不可用 → loaded=False 空快照。"""
    global _last_rows
    from sqlalchemy import text

    from backend.memory.database import get_session
    from backend.infra.mcp_registry import specs as spec_lib

    try:
        async for session in get_session():
            crow = (await session.execute(text(_SELECT_CONNECTIONS))).mappings().all()
            trow = (await session.execute(text(_SELECT_TOOLS))).mappings().all()
            grow = (await session.execute(text(_SELECT_GRANTS))).mappings().all()
            break
        else:
            raise RuntimeError("get_session 未产出会话")
    except Exception:
        logger.warning(
            "[McpRegistry] DB 读取失败，保留上次快照（表未建时先执行 "
            "python scripts/init_db.py，宿主机加 --port 5433）", exc_info=True,
        )
        return McpRegistrySnapshot(loaded=False)

    _last_rows = {
        "connections": [dict(r) for r in crow],
        "tools": [dict(r) for r in trow],
        "grants": [dict(r) for r in grow],
    }

    connections: dict[str, McpConnectionRuntime] = {}
    for row in crow:
        connections[str(row["id"])] = McpConnectionRuntime(
            connection_id=str(row["id"]),
            base_url=str(row["base_url"] or ""),
            auth_type=str(row["auth_type"] or "none"),
            network_scope=str(row["network_scope"] or "public"),
            secret_cipher=str(row["secret_cipher"] or ""),
        )

    tools: dict[str, McpToolRuntime] = {}
    decls: dict[str, Any] = {}
    errors: list[tuple[str, str]] = []
    tool_ids: dict[int, str] = {}
    capability_names: list[str] = []
    for row in trow:
        slug = str(row["capability_slug"] or "")
        tool_id = int(row["id"])
        fallback_name = f"tool#{tool_id}"
        if not spec_lib.is_valid_slug(slug):
            errors.append((fallback_name, f"capability_slug 非法: {slug!r}"))
            continue
        capability = f"{_CAPABILITY_PREFIX}{slug}"
        capability_names.append(capability)
        tool_ids[tool_id] = capability
    conflicts = _static_capability_conflicts(capability_names)
    if conflicts:
        for capability in conflicts:
            errors.append((capability, "与静态注册表 capability 重名"))
            tool_ids = {
                tid: cap for tid, cap in tool_ids.items() if cap not in conflicts
            }

    rows_by_id = {int(row["id"]): row for row in trow}
    for tool_id, capability in tool_ids.items():
        row = rows_by_id[tool_id]
        conn = connections.get(str(row["connection_id"]))
        if conn is None:
            errors.append((capability, "所属连接未启用或不存在"))
            continue
        try:
            spec = spec_lib.derive_tool_spec(
                capability,
                description=str(row["description"] or ""),
                input_schema=row["input_schema"],
                governance=row["governance"],
            )
            decl = spec_lib.derive_capability_decl(
                capability,
                description=str(row["description"] or ""),
                input_schema=row["input_schema"],
                governance=row["governance"],
                examples=row["examples"],
            )
        except spec_lib.SpecDeriveError as exc:
            errors.append((capability, str(exc)))
            continue
        tools[capability] = McpToolRuntime(
            capability=capability,
            tool_id=tool_id,
            connection_id=conn.connection_id,
            upstream_tool_name=str(row["tool_name"] or ""),
            tenant_id=str(row["tenant_id"] or "default"),
            spec=spec,
            network_scope=conn.network_scope,
            content_digest=derived_content_digest(
                row["description"], row["input_schema"],
                row["governance"], row["examples"]),
        )
        decls[capability] = decl

    grants: set[tuple[str, str, str]] = set()
    for row in grow:
        capability = tool_ids.get(int(row["tool_id"]))
        if not capability:
            continue
        grants.add((
            str(row["product_key"]),
            capability,
            str(row["tenant_id"] or "default"),
        ))

    import time as _time

    return McpRegistrySnapshot(
        loaded=True,
        tools=tools,
        connections=connections,
        capability_decls=decls,
        grants=frozenset(grants),
        errors=tuple(errors),
        refreshed_at=_time.time(),
    )


async def refresh(force_invalidate: bool = False) -> bool:
    """拉取 DB 并交换进程内快照；签名变化时触发失效回调。"""
    global _snapshot, _last_signature
    snap = await load_snapshot()
    if not snap.loaded:
        return False
    signature = snap.signature()
    with _lock:
        changed = signature != _last_signature or force_invalidate
        _snapshot = snap
        if changed:
            _last_signature = signature
    if changed:
        logger.info(
            "[McpRegistry] 快照已更新: %d 个可执行工具 / %d 个授权 / %d 个连接",
            len(snap.tools), len(snap.grants), len(snap.connections),
        )
        _invalidate_consumers()
    return True


async def refresh_now() -> bool:
    """管理端写操作后立即刷新本进程（其余 worker 靠轮询收敛）。"""
    return await refresh(force_invalidate=True)


async def refresh_loop(interval: float = _REFRESH_INTERVAL_S) -> None:
    """后台轮询（server startup 挂载；首轮立即拉取，异常不退出）。"""
    while True:
        try:
            await refresh()
        except Exception:
            logger.warning("[McpRegistry] 刷新循环异常，保留上次快照", exc_info=True)
        await asyncio.sleep(interval)


def product_granted(product_id: str, capability: str, tenant_id: str) -> bool:
    """产品授权检查（product_allows_capability 的动态分支消费）。"""
    if not capability.startswith(_CAPABILITY_PREFIX):
        return False
    return (product_id, capability, tenant_id or "default") in snapshot().grants


# 写操作强一致复核：命中行 = 审核有效 + 未漂移 + 连接启用 + 产品授权在册
_VERIFY_WRITE_SQL = """
SELECT t.id
FROM ai.mcp_tools t
JOIN ai.mcp_connections c ON c.id = t.connection_id AND c.enabled
JOIN ai.mcp_tool_grants g
  ON g.tool_id = t.id AND g.enabled AND g.revoked_at IS NULL
 AND g.tenant_id = %(tenant)s AND g.product_key = %(product)s
WHERE t.capability_slug = %(slug)s AND t.tenant_id = %(tenant)s
  AND t.status = 'approved' AND t.schema_changed = false
LIMIT 1
"""


def verify_write_authorization(
    capability: str, product_id: str, tenant_id: str,
) -> tuple[bool, str]:
    """写操作（operation != read）执行前的 DB 直查强一致校验。

    进程内快照是 15s 轮询的最终一致视图——撤权/停用/漂移在其他 worker
    可能仍有最长一个周期的旧授权。带副作用的调用不能依赖该窗口，此处在
    出网前按事实源（ai.mcp_* 表）原子复核；internal 产品（管理端治理
    验证通道）不依赖 grants，由调用方另行判定。DB 不可用时 fail-closed。
    """
    if not capability.startswith(_CAPABILITY_PREFIX):
        return False, f"非动态能力: {capability}"
    try:
        from backend.infra.db import get_memory_engine

        engine = get_memory_engine()
        raw = engine.raw_connection()
        try:
            with raw.cursor() as cur:
                cur.execute(_VERIFY_WRITE_SQL, {
                    "slug": capability[len(_CAPABILITY_PREFIX):],
                    "tenant": tenant_id or "default",
                    "product": product_id,
                })
                if cur.fetchone() is not None:
                    return True, ""
                return False, (
                    "授权/审核/连接状态在事实源中已失效"
                    "（撤权、停用、漂移或未授权）")
        finally:
            raw.rollback()  # 只读复核，不留事务
            raw.close()
    except Exception:  # noqa: BLE001 — 强一致校验失败按拒绝
        logger.warning(
            "[McpRegistry] 写操作强一致复核失败（fail-closed）: %s",
            capability, exc_info=True)
        return False, "授权复核不可用，按拒绝处理"


def get_runtime(capability: str) -> McpToolRuntime | None:
    return snapshot().tools.get(capability)


def dynamic_capabilities() -> list[str]:
    return sorted(snapshot().tools.keys())


def resolve_headers(capability: str) -> dict[str, str] | None:
    """调用点解密连接凭据 → HTTP 头。解密失败显式抛错（绝不静默无凭据调用）。"""
    runtime = get_runtime(capability)
    if runtime is None:
        return None
    conn = snapshot().connections.get(runtime.connection_id)
    if conn is None or conn.auth_type != "static_bearer" or not conn.secret_cipher:
        return None
    from backend.shared.crypto import SecretCryptoError, decrypt_secret

    try:
        secret = decrypt_secret(conn.secret_cipher)
    except SecretCryptoError:
        logger.error(
            "[McpRegistry] 连接 %s 凭据解密失败，拒绝调用（密钥轮换或密文损坏）",
            runtime.connection_id,
        )
        raise
    return {"Authorization": f"Bearer {secret}"}


def inject_snapshot_for_tests(snap: McpRegistrySnapshot) -> None:
    """测试态注入快照（绕过 DB）。"""
    global _snapshot, _last_signature
    with _lock:
        _snapshot = snap
        _last_signature = snap.signature()


def reset_for_tests() -> None:
    global _snapshot, _last_signature, _last_rows
    with _lock:
        _snapshot = McpRegistrySnapshot()
        _last_signature = ""
    _last_rows = {}


__all__ = [
    "McpConnectionRuntime",
    "McpRegistrySnapshot",
    "McpToolRuntime",
    "derived_content_digest",
    "dynamic_capabilities",
    "get_runtime",
    "inject_snapshot_for_tests",
    "last_rows",
    "load_snapshot",
    "product_granted",
    "refresh",
    "refresh_loop",
    "refresh_now",
    "register_on_change",
    "reset_for_tests",
    "resolve_headers",
    "snapshot",
    "verify_write_authorization",
]
