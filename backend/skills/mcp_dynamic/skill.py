"""skills/mcp_dynamic — 外部 MCP 动态工具的统一执行 Skill（ADR-0011 D4）。

所有 `mcp.*` capability 共用本节点：启动时注册一次，运行时按 step 的
capability 从 mcp_registry 快照解析连接、凭据引用与上游工具名，经
BaseSkill.execute → governance_runtime.execute → SafeToolExecutor 的
既有治理全链调用外部 MCP server。不为每个动态工具注册 Skill 或重建图。
"""
from __future__ import annotations

import json
from typing import Any, ClassVar

from backend.infra.mcp_client import McpClientError, call_tool
from backend.shared.logger import logger
from backend.skills.base import BaseSkill


class _McpDynamicToolAdapter:
    """LangChain Tool 等效适配器：capability → 外部 MCP 调用（同步桥）。

    每次调用重新读快照——停用/漂移/撤权后的工具在这里拿不到 runtime，
    显式失败而不是静默继续。
    """

    def __init__(self, capability: str) -> None:
        self._capability = capability

    @property
    def name(self) -> str:
        return self._capability

    def invoke(self, params: dict) -> Any:
        from backend.core.request_context import (
            get_current_product_id,
            get_tool_tenant_id,
        )
        from backend.infra.mcp_registry import store as mcp_store
        from backend.tools.url_guard import UrlBlockedError, assert_url_allowed

        runtime = mcp_store.get_runtime(self._capability)
        if runtime is None:
            raise McpClientError(
                f"MCP 工具未生效或已停用: {self._capability}")
        conn = mcp_store.snapshot().connections.get(runtime.connection_id)
        if conn is None:
            raise McpClientError(
                f"MCP 连接未启用: {runtime.connection_id}")
        # 写操作出网前按事实源强一致复核（ADR-0011 D2 安全语义）：快照是
        # 15s 轮询的最终一致视图，撤权/停用/漂移在其他 worker 仍可能有旧
        # 授权——带副作用的调用不接受该窗口。internal（管理端治理验证）
        # 不依赖产品授权快照，跳过 grants 复核。
        if str(runtime.spec.operation.value) != "read":
            product_id = get_current_product_id() or "internal"
            if product_id != "internal":
                ok, reason = mcp_store.verify_write_authorization(
                    self._capability, product_id, get_tool_tenant_id())
                if not ok:
                    logger.warning(
                        "[mcp_dynamic] 写操作强一致拦截: capability=%s "
                        "product=%s 原因=%s",
                        self._capability, product_id, reason)
                    raise McpClientError(
                        f"写操作被拒绝（{reason}）: {self._capability}")
        # SSRF 运行时兜底：连接登记时已校验，此处防 DNS rebinding（每次
        # 调用重新解析目标 IP；private scope 才放行内网地址）。
        try:
            assert_url_allowed(
                conn.base_url,
                allow_private=conn.network_scope == "private",
            )
        except UrlBlockedError as exc:
            raise McpClientError(f"MCP 目标地址被 SSRF 防护拦截: {exc}") from exc
        headers = mcp_store.resolve_headers(self._capability)
        payload = call_tool(
            conn.base_url,
            runtime.upstream_tool_name,
            dict(params or {}),
            timeout=runtime.spec.timeout_ms / 1000.0,
            ttl=0.0,  # 动态工具结果语义未知且可能含写操作，一律不缓存
            headers=headers,
            min_interval=0.0,  # 节流/限流由治理层统一执行
        )
        return _wrap_payload(payload)


def _wrap_payload(payload: Any) -> Any:
    """MCP 归一 payload → Skill 输出契约兼容形态。

    dict 原样（含上游封套语义，validate_semantics 识别失败封套）；
    list 包一层 {"data": ...}（结构化契约要求 dict）；None 视为空结果；
    str / 标量保真。
    """
    if payload is None:
        return {"status": "empty"}
    if isinstance(payload, (dict, str)):
        return payload
    return {"data": payload}


class DynamicMcpSkill(BaseSkill):
    """动态 MCP 工具的统一 Skill 壳。

    不参与 skills/registry 的静态实例注册（bind_manifest_metadata 要求
    manifest capability，动态面无此约束）；capability → 节点的映射由
    capability_registry.get_node 的 mcp.* 特判完成。
    """

    name: str = "mcp_dynamic"
    capabilities: ClassVar[list[str]] = []
    # MCP payload 以结构化 JSON 为主（上游 structuredContent / JSON 文本）
    output_type: ClassVar[str] = "structured"
    default_timeout: ClassVar[float] = 15.0
    default_max_retries: ClassVar[int] = 0

    @property
    def _tool_fn(self) -> Any:
        raise NotImplementedError(
            "DynamicMcpSkill 按 capability 动态分发，见 _select_tool")

    def _select_tool(self, capability: str, params: dict) -> tuple[Any, dict]:
        return _McpDynamicToolAdapter(capability), params

    def _validate_params(self, params: dict, capability: str = "") -> str | None:
        """参数校验直接用治理快照的 ToolSpec（权威 Schema 在快照里）。"""
        from backend.core.tool_governance.schema_validator import (
            SchemaValidationError,
            validate_json_schema,
        )
        from backend.infra.mcp_registry import store as mcp_store

        runtime = mcp_store.get_runtime(capability)
        if runtime is None:
            return f"MCP 工具未生效或已停用: {capability}"
        try:
            validate_json_schema(dict(params or {}), runtime.spec.params_schema)
            return None
        except SchemaValidationError as exc:
            return str(exc)

    def _normalize_output(self, capability: str, output: Any) -> Any:
        # adapter 已归一（dict/str）；复用基类 structured 分支的封套解包
        return super()._normalize_output(capability, output)

    def _describe(self, capability: str) -> str:
        from backend.infra.mcp_registry import store as mcp_store

        runtime = mcp_store.get_runtime(capability)
        if runtime is None:
            return "（工具未生效）"
        spec = runtime.spec
        return json.dumps({
            "capability": capability,
            "operation": spec.operation.value,
            "risk": spec.risk_level.value,
            "tool_id": runtime.tool_id,
        }, ensure_ascii=False)


_skill = DynamicMcpSkill()


async def mcp_dynamic_skill_node(state: dict) -> dict:
    """LangGraph 节点：与静态 Skill 节点同签名（state → step_results）。"""
    logger.debug("[mcp_dynamic] node invoked")
    return await _skill.execute(state)


__all__ = ["DynamicMcpSkill", "mcp_dynamic_skill_node"]
