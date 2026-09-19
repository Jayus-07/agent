"""services/sys_config.py — 灰度开关动态配置（Lite 版，2026-09-16）

把守卫类开关从「env + 重启」升级为「DB 覆盖 + 进程内缓存 + env 兜底」，
不引入 Nacos/Apollo 等配置中心组件（当前单实例后端，TTL 级延迟可接受）。

设计不变量（见 docs/2026-09-16-动态配置lite与API-Key多Key化方案.md）：

1. 存储分层：sys_config 表是覆盖层；表内无记录 = 用 env 默认值。
   env 语义不变（部署兜底 + 应急），DB 改动免重启生效。
2. 读取零阻塞：守卫热路径调 get_mode()，只读进程内 _values（纯 dict），
   绝不做同步 IO；DB 轮询由后台 refresh_loop 完成（默认 15s 一轮）。
3. fail-closed：任何异常路径（DB 不可用 / 值非法 / 键未知）都回退
   「上次已知值 → env 默认」，绝不放出一个未知值给守卫；非法值记 error 告警
   且不进缓存（宁可误拦不可漏放——env 默认即当前提交态的安全缺省）。
4. 审计含旧值：set_value 落 sys_config_history（old→new + 操作人），
   叠加路由层 [SysConfigAudit] 结构化日志与网关访问日志（who/when）。
   回滚 = 把旧值写回（历史表里查得到旧值）。

多实例注意：刷新靠各实例自身 15s 轮询 + 本实例 PUT 后即时更新缓存；
其他实例最坏有 1 个 TTL 的生效延迟（灰度开关场景可接受）。
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from sqlalchemy import text

from backend.memory.database import get_session
from backend.shared.logger import logger

# ── 开关注册表（唯一事实源）─────────────────────────────────
# env_key: {allowed: 合法值白名单, default: env 未设置时的缺省, desc: 说明}
# 新增守卫开关：在此登记 → 路由读 get_mode() → 前端 overview 自动可切。
#
# 登记项可选扩展字段（2026-09-19 新增，供模型角色等非守卫登记项使用，
# 守卫开关不使用、行为与历史完全一致）：
#   case_sensitive: True 时不做小写归一（模型名大小写敏感）
#   validator:      可调用 (str) -> bool，替代 allowed（合法集动态变化时用）
# 详见 docs/model-config-governance-design.md §1.1。
#
# ⚠️ 模型角色**不登记在本表**，而是登记在 backend/config/model_roles.py：
#   1. 本表是守卫开关注册表，GET /sys/config 的返回集合被测试断言为恰好两项
#      （tests/api/test_sys_config_admin.py::test_get_config_lists_registered_switches）；
#   2. 模型名大小写敏感、合法集来自 AVAILABLE_MODELS，需要上面两个扩展点；
#   3. 模型角色的解析入口是 model_roles.resolve_model，热路径在配置导入链上，
#      不能依赖本模块（本模块 import SQLAlchemy + asyncio，会拖重配置导入）。

_SWITCHES: dict[str, dict[str, Any]] = {
    "JWT_SESSION_GUARD_MODE": {
        "allowed": ("off", "audit", "enforce"),
        "default": "audit",
        "desc": "Bearer 会话闸（后端中间件第二道校验）处置模式",
    },
    "SENSITIVE_API_GUARD_MODE": {
        "allowed": ("audit", "enforce"),
        "default": "enforce",
        "desc": "敏感端点统一守卫（require_user_actor / require_admin_user）处置模式",
    },
}

_REFRESH_INTERVAL_S = 15.0

# 进程内缓存：key → 已校验合法的生效值。只有 refresh_once / set_value 写入，
# 写入值必经白名单校验，因此 get_mode 的返回值永远属于 allowed 集合。
_values: dict[str, str] = {}
_db_meta: dict[str, dict[str, Any]] = {}   # key → {updatedAt, updatedBy}（DB 覆盖层元信息）
_refresh_lock = asyncio.Lock()


def _normalize_with(spec: dict[str, Any] | None, raw: Any) -> str | None:
    """通用值校验（spec 形状见 _SWITCHES，扩展点见模块内说明）。

    - 静态白名单：`spec["allowed"]` 元组
    - 函数式校验：`spec["validator"]`（可调用，与 allowed 二选一）——
      用于**合法集随代码变化**的登记项，如模型角色（合法集 = AVAILABLE_MODELS）
    - 大小写：`case_sensitive=True` 时保留原大小写。模型名
      `MiniMax-M3` / `Qwen/Qwen3-32B` / `BAAI/bge-m3` 一律不得被 lower 破坏；
      缺省沿用守卫开关的历史行为（小写归一）。

    非法值一律返回 None，由调用方回退默认并告警（fail-closed）。
    """
    if spec is None:
        return None
    value = str(raw) if raw is not None else ""
    value = value.strip() if spec.get("case_sensitive") else value.strip().lower()
    validator = spec.get("validator")
    if validator is not None:
        return value if validator(value) else None
    return value if value in spec.get("allowed", ()) else None


def _env_default(env_key: str) -> str:
    """env 兜底值（校验，非法 env 值回落注册表缺省）。"""
    spec = _SWITCHES.get(env_key)
    if spec is None:
        return ""
    return _normalize_with(spec, os.getenv(env_key, "")) or spec["default"]


def get_mode(env_key: str) -> str:
    """守卫热路径读取（同步、零 IO、不抛异常）。

    优先级：DB 覆盖值（进程内缓存）→ env 默认值。
    未知键（未在注册表登记）不动态放行——回退 env 原值语义，交由调用方默认。
    """
    if env_key in _values:
        return _values[env_key]
    spec = _SWITCHES.get(env_key)
    if spec is None:
        return os.getenv(env_key, "").strip().lower()
    return _env_default(env_key)


def get_info(env_key: str) -> dict[str, Any]:
    """生效值 + 来源（供 overview 展示，不触发 IO）。"""
    spec = _SWITCHES.get(env_key)
    if spec is None:
        return {"mode": os.getenv(env_key, "").strip().lower(),
                "source": "env", "allowed": [], "updatedAt": None, "updatedBy": None}
    if env_key in _values:
        meta = _db_meta.get(env_key) or {}
        return {"mode": _values[env_key], "source": "db",
                "allowed": list(spec.get("allowed", ())),
                "updatedAt": meta.get("updatedAt"), "updatedBy": meta.get("updatedBy")}
    return {"mode": _env_default(env_key), "source": "env-default",
            "allowed": list(spec.get("allowed", ())), "updatedAt": None, "updatedBy": None}


def _normalize(env_key: str, raw: Any) -> str | None:
    """白名单校验；非法返回 None（调用方回退默认并告警）。"""
    return _normalize_with(_SWITCHES.get(env_key), raw)


async def _ensure_tables(session) -> None:
    """自建表（同 competitor/store.py 约定：CREATE TABLE IF NOT EXISTS 幂等）。"""
    await session.execute(text(
        "CREATE TABLE IF NOT EXISTS sys_config ("
        " key VARCHAR(64) PRIMARY KEY,"
        " value VARCHAR(128) NOT NULL,"
        " updated_by VARCHAR(64),"
        " updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"))
    await session.execute(text(
        "CREATE TABLE IF NOT EXISTS sys_config_history ("
        " id BIGSERIAL PRIMARY KEY,"
        " key VARCHAR(64) NOT NULL,"
        " old_value VARCHAR(128),"
        " new_value VARCHAR(128),"
        " operator VARCHAR(64) NOT NULL,"
        " changed_at TIMESTAMPTZ NOT NULL DEFAULT now())"))
    await session.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_sys_config_history_key "
        " ON sys_config_history (key, changed_at DESC)"))


async def _fetch_overrides(session) -> dict[str, dict[str, Any]]:
    """读 DB 覆盖层：key → {value, updatedAt, updatedBy}（仅注册表内的键）。"""
    rows = (await session.execute(text(
        "SELECT key, value, updated_by, updated_at FROM sys_config "
        "WHERE key = ANY(:keys)"),
        {"keys": list(_SWITCHES)})).mappings().all()
    return {r["key"]: {"value": r["value"], "updatedBy": r["updated_by"],
                       "updatedAt": r["updated_at"].isoformat() if r["updated_at"] else None}
            for r in rows}


async def refresh_once() -> bool:
    """拉取 DB 覆盖层并刷新进程内缓存。失败保留上次已知值（fail-closed）。

    返回是否成功。非法 DB 值：记 error 告警、不进缓存（该键维持原生效值）。
    """
    async with _refresh_lock:
        try:
            async for session in get_session():
                await _ensure_tables(session)
                overrides = await _fetch_overrides(session)
                break
        except Exception:
            logger.warning("[SysConfig] 覆盖层读取失败，守卫开关维持上次已知值"
                           "（无历史值时用 env 默认）", exc_info=True)
            return False
        for key, meta in overrides.items():
            valid = _normalize(key, meta["value"])
            if valid is None:
                logger.error(
                    "[SysConfig] DB 中 %s 的值 %r 不在白名单内，已忽略并维持 %s"
                    "（请改回合法值：GET /sys/config）",
                    key, meta["value"], get_mode(key))
                _db_meta.pop(key, None)
                continue
            _values[key] = valid
            _db_meta[key] = {k: meta[k] for k in ("updatedAt", "updatedBy")}
        for key in list(_values):
            if key not in overrides:
                _values.pop(key, None)   # 覆盖记录被删除 → 回落 env 默认
                _db_meta.pop(key, None)
        return True


async def refresh_loop(interval: float = _REFRESH_INTERVAL_S) -> None:
    """后台轮询循环（server startup 挂载；首轮立即拉取，异常不退出）。"""
    while True:
        await refresh_once()
        await asyncio.sleep(interval)


async def set_value(env_key: str, value: str, operator: str) -> dict[str, Any]:
    """写入覆盖值 + 审计历史（旧值→新值），并即时刷新进程内缓存。

    返回 {old, new}；old 为 None 表示此前无 DB 覆盖（生效的是 env 值）。
    """
    spec = _SWITCHES.get(env_key)
    if spec is None:
        raise KeyError(f"未登记的配置键: {env_key}")
    new = _normalize(env_key, value)
    if new is None:
        raise ValueError(f"{env_key} 合法值为 {'/'.join(spec['allowed'])}，收到 {value!r}")

    old: str | None = None
    async for session in get_session():
        await _ensure_tables(session)
        row = (await session.execute(text(
            "SELECT value FROM sys_config WHERE key = :k"), {"k": env_key})).first()
        if row is not None:
            old = row[0]
        await session.execute(text(
            "INSERT INTO sys_config (key, value, updated_by) VALUES (:k, :v, :op) "
            "ON CONFLICT (key) DO UPDATE SET value = :v, updated_by = :op, updated_at = now()"),
            {"k": env_key, "v": new, "op": operator})
        await session.execute(text(
            "INSERT INTO sys_config_history (key, old_value, new_value, operator) "
            "VALUES (:k, :o, :n, :op)"),
            {"k": env_key, "o": old, "n": new, "op": operator})
        await session.commit()
        break

    _values[env_key] = new
    _db_meta[env_key] = {"updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                         "updatedBy": operator}
    # 审计含旧值：old=None 表示此前生效的是 env 值（回滚基准 = env 默认）
    old_display = old if old is not None else f"env:{_env_default(env_key)}"
    logger.warning(  # 守卫开关变更有安全语义，warning 级保证不被降噪吞掉
        "[SysConfigAudit] actor=%s action=set key=%s old=%s new=%s",
        operator, env_key, old_display, new)
    return {"old": old, "new": new}


async def list_effective() -> list[dict[str, Any]]:
    """全部登记键的生效状态（GET /sys/config 用；顺带触发一次刷新兜底）。"""
    await refresh_once()
    out = []
    for env_key, spec in _SWITCHES.items():
        info = get_info(env_key)
        out.append({"key": env_key, "description": spec["desc"],
                    "allowed": list(spec["allowed"]), **info})
    return out


def reset_cache_for_tests() -> None:
    """测试态注入点：清空进程内缓存，恢复 env 默认语义。"""
    _values.clear()
    _db_meta.clear()
