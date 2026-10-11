# MCP P0 整改发现的 Tool 治理缺口清单（供第 4 阶段「统一 Tool Governance 与管理中心 V1」T0 审计使用）

- 来源：PR #27（task/mcp-p0-security-20261011）MCP P0 安全整改与联合复审返修，2026-10-11
- 范围纪律：本清单只记录，不在本轮整改；均不导致已合入的 MCP P0 修复被绕过
- 基线：main@c79e8a8 + PR #27（返修后 420a2de/43ad5aa）

## 一、Tool Registry 维度

| # | 缺口 | 源码定位 | 说明与建议 |
|---|---|---|---|
| R-1 | `governance_spec is None` 兜底路径：无 spec 能力绕过治理门直跑 SafeToolExecutor | `backend/skills/base.py:524-539` | 实测当前 tool_registry 25 个 Tool 全部有 spec（差集为空，2026-10-11 实测）；风险在未来新增 Tool 不登记 spec 时静默走兜底。建议启动期/CI 增加「registry ∧ capabilities.yaml spec 一致性」守卫（审计 P1-1） |
| R-2 | Registry（25）与 capabilities.yaml specs（57）双向一致性无 CI 门，数量差无解释义务 | `backend/tools/tool_registry.py`、`backend/orchestration/router/capabilities.yaml`、`backend/core/tool_governance/registry.py` | spec 含编排层 capability（非 tools/ 包），属合法差异但无机制区分「合法差异」与「漏登记」 |
| R-3 | MCP server 手写 tool（list_documents/get_stats）无 capability/spec 对应物 | `mcp_servers/servers/rag.py` | 本轮以 server 层授权（RagAuthorization）+ 治理链（仅 search_knowledge 有 spec）覆盖；管理中心 V1 需决定 MCP 手写工具的 spec 登记形态 |

## 二、Executor 维度

| # | 缺口 | 源码定位 | 说明与建议 |
|---|---|---|---|
| E-1 | `TOOL_RUNTIME_ENABLED=false` 时回退旧执行循环，绕过全部治理且无审计（fail-open） | `backend/skills/base.py:686、813-814` | 审计 P1-2；建议关闭时写操作能力直接拒绝 |
| E-2 | `execute_sync` 要求调用线程无 running loop，属"约定"非"机制" | `backend/core/tool_governance/guard.py:302-318` | 12306 事故（同步 Tool 内嵌 asyncio.run）与 PR#27 踩坑（FastMCP loop 线程调同步工具）同源；MCP 侧已用 async+to_thread 修复，建议 guard 层加 running-loop 检测 fail-fast |
| E-3 | Budget fail-open：rag-service /ask 预算绑定失败仅告警放行 | `backend/services/rag_server.py:306-316` | 审计 P1-5 关联；归预算批次 |

## 三、权限治理维度

| # | 缺口 | 源码定位 | 说明与建议 |
|---|---|---|---|
| P-1 | `list_tables` 无资源级权限门（白名单表名，入口已 401/internal-token 挡匿名；机器主体持 sql.read 即可列） | `mcp_servers/servers/sql.py:87-91` | 低敏（schema_loader 白名单），管理中心 V1 统一裁决时补 |
| P-2 | MCP search_knowledge 的 session_id 共享 "default"（remote 形态记忆/历史串扰面） | `backend/tools/rag.py:57（_get_session_id 空值）`、`backend/rag/client.py` | 非身份问题；MCP 协议有 session 概念，待拍板映射策略 |
| P-3 | observability 演示入口 `MultiAgentSystem().ask` 直调 RAG，权限参数透传不足（有 require_user_actor JWT 门，非匿名） | `backend/app/api/routes/observability.py:664` | RAG 域后续项 |
| P-4 | Java 通道工具白名单 `AI_TOOLS_ALLOWED` 为静态 env 串，无 per-tool 风险分级/动态管控 | `backend/config/messaging.py:60`、`backend/app/api/routes/internal_ai.py:31-38` | 管理中心 V1 的动态白名单/分级输入 |

## 四、观测维度

| # | 缺口 | 源码定位 | 说明与建议 |
|---|---|---|---|
| O-1 | REST MCP 通道（manager.route）工具执行无 trace span、治理事件（timeout/fail/budget）不落 span events（本轮 execute_sync 未接 on_event，trace_span=None） | `mcp_servers/servers/rag.py`（execute_sync 调用点）、`backend/core/tool_governance/guard.py:226/284`、`mcp_servers/manager.py:88-91` | 对齐 live_search_service 的 start_tool_span/finish_tool_span 样板即可补齐 |
| O-2 | REST `/mcp/call` 无结构化审计行（对比 :8091 审计中间件有 key 指纹/tool/状态/耗时） | `backend/app/api/routes/mcp.py`（仅 logger.info） | 建议对齐 protocol_app 的 mcp_audit_middleware 形态 |
| O-3 | `get_stats` 管理员访问无专门审计留痕（谁能看诊断统计） | `mcp_servers/servers/rag.py`（admin 分支） | 敏感面访问建议 logger.warning 级留痕（actor+tenant） |

## 五、本轮已确认的 PASS 基线（T0 审计的对照起点）

- MCP 四入口（REST/8091/Java/consistency）鉴权面：REST require_identity 401；8091 X-API-Key fail-closed+机器主体；Java internal-token+白名单；consistency 只读。
- RAG 授权单一裁决源：HTTP `/api/rag/*` 与 MCP 通道共用 `RagAuthorization`；search_knowledge 双入口（Skill/MCP）共用 governance_runtime + tool 本体权限链；SQL 双入口共用 SQLAgent 策略链。
- 失败语义：REST ok:false / 标准 MCP isError:true，业务失败与协议成功分离。
- remote 形态权限透传：tools/rag.py → rag-service /ask 全量透传（含 roles）。
- 日志：params/问题/回答原文零落日志（长度指纹）。
