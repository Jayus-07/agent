# MCP P0 安全整改 — 独立审查包（供 GPT 复核）

- 生成时间：2026-10-11（随整改逐轮更新，本文件已随 PR 提交）
- 分支：`task/mcp-p0-security-20261011`
- 审查基线演进：首轮/返修基线 `49f26f2` → 第三轮收口 `5d4fe9d` → 第四轮复核修正 `903c7e2` → **终轮（RagAuthorizationError 污染路径围堵，HEAD 见 PR 页面最新 commit）**
- 交付状态：**READY_FOR_REVIEW（终轮阻断已修复）**；各轮历史测试记录在第 6 节分轮保留
- 历史标注说明：文件早期版本曾标注"未 commit"，自第三轮起已按指令随 PR 分支提交，GitHub 可读

---

## 1. PR 信息

| 项 | 值 |
|---|---|
| PR | https://github.com/Jayus-07/agent/pull/27 |
| 状态 | OPEN / MERGEABLE（未合并） |
| base | `main` @ `c79e8a8f49443997d47134ffd3146533b755e0eb` |
| head | `task/mcp-p0-security-20261011` @ `49f26f2310957f7a4e7cd52f440a4b9b26aa33ad` |
| commits | `1655433`（REST 入口鉴权+日志脱敏）→ `53487a6`（RAG server 授权/脱敏/旁路）→ `b763ce6`（定向测试 13）→ `420a2de`（联合复审返修源码）→ `43ad5aa`（返修验收测试 19+适配）→ `49f26f2`（治理缺口清单文档） |

**变更文件（12 个，+1036/−63）**：

| 文件 | 变更 |
|---|---|
| `backend/app/api/routes/mcp.py` | 三端点 require_identity 401 + 身份五元组全量绑定 + params 日志指纹化 |
| `backend/app/api/routes/internal_ai.py` | Java 通道 actor 委托五元组显式绑定（未携带维度置空） |
| `backend/security/service_identity.py` | 新增 `MCP_PROTOCOL_USER_ID` / `bind_mcp_protocol_identity()` |
| `backend/tools/rag.py` | roles→Principal→ask_result 全链传递；问题/回答原文日志改长度指纹 |
| `mcp_servers/servers/rag.py` | RagAuthorization 授权下推；get_stats admin 门+脱敏；search_knowledge 经 governance_runtime.execute_sync；失败语义 raise；行白名单 |
| `mcp_servers/servers/sql.py` | 失败语义协议级 raise（service_unavailable/permission_denied/其他失败） |
| `mcp_servers/protocol_app.py` | 8091 工具执行绑定机器主体；_fn 改 async+to_thread |
| `backend/tests/test_mcp_security.py` | 新增定向 13 例（3 例返修时断言语义升级） |
| `backend/tests/test_mcp_returnfix_acceptance.py` | 新增返修验收 19 例 |
| `backend/tests/test_mcp_protocol.py` | _fn 同步→异步调用适配（1 例） |
| `backend/tests/sql/test_sql_production_closure.py` | 2 例失败语义断言升级（错误 dict→协议级异常） |
| `docs/reports/2026-10-11-MCP-P0-tool-governance-gaps.md` | 治理缺口清单（R/E/P/O 四维） |

## 2. P0 整改对照表（原始漏洞 → 根因 → 修复位置 → 测试）

| 原始漏洞 | 根因 | 修复位置（文件:行号，HEAD 实测） | 对应测试 |
|---|---|---|---|
| P0-1② `/api/mcp/call` 匿名执行任意 tool；tools/servers 匿名枚举 | `resolve_identity` 失败仅跳过 ContextVar 绑定，不拦截，继续 `manager.route()` | `backend/app/api/routes/mcp.py:24`（router 级 `dependencies=[Depends(require_identity)]`）、`:59`（handler 注入 ident） | `test_mcp_tools/servers/call_requires_identity`（401）；`test_mcp_tools_authenticated_ok` |
| P0-1① `get_stats` 返回 CHROMA_PATH/DOC_DB_PATH/EMBEDDING_MODEL_PATH 服务器绝对路径，且匿名可调 | server 直接返回 config 路径常量，无授权无脱敏 | `mcp_servers/servers/rag.py:175-190`（`_tool_authorization` + `authz.is_admin` 门 + 脱敏统计体，路径字段删除） | `test_get_stats_anonymous_denied`、`test_get_stats_non_admin_denied`、`test_get_stats_admin_sanitized`（断言 `/srv` 不出现、无 path 键）、`test_get_stats_admin_scope_excludes_other_kb` |
| P1-3 `search_knowledge` 直调 `pipeline.ask()` 权限参数全空 | server 层绕过 tool 本体，授权矩阵失效 | `mcp_servers/servers/rag.py:107-147`（`governance_runtime.execute_sync(ToolCallRequest(capability="search_knowledge_tool"))`，失败 `:142` raise） | `test_search_knowledge_executes_via_tool_body`、`test_ask_result_receives_roles_and_principal` |
| 资源授权缺失：`list_documents` 无授权全量返回原始行（含 file_path） | 直调 `registry.search(keyword)/list_active()` 无过滤 | `mcp_servers/servers/rag.py:152-168`（RagAuthorizationError→`_deny` raise；kb_scope/visible_scopes 下推；`_DOC_ROW_PUBLIC_FIELDS` 白名单 `:41-57`） | `test_list_documents_unauthorized_denied`、`test_list_documents_scope_pushdown_and_path_stripped`、`test_viewer_without_permissions_cannot_see_restricted`、`test_admin_sees_restricted` |
| 敏感日志：params 原文、RAG 问题/回答原文落日志 | f-string 全量打印 | `backend/app/api/routes/mcp.py:48-52,90`（`_params_fingerprint`）；`backend/tools/rag.py:47,93`（仅 `len=`） | `test_mcp_call_log_omits_params_raw`、`test_no_question_or_answer_text_in_logs` |
| 8091 API Key 被当作用户身份（无执行主体） | 工具函数直接 `server.call_tool`，ContextVar 全空→未声明主体 | `mcp_servers/protocol_app.py:97-115`（`_fn` 绑定 `bind_mcp_protocol_identity`）；`backend/security/service_identity.py:78-110` | `test_search_runs_as_service_principal`、`test_service_principal_has_no_rag_admin`、`test_get_stats_denied_never_leaks_stats` |
| 失败被包装为协议成功（ok:true 内嵌 error dict / isError:false） | server 返回错误 dict，manager 包 ok:true | rag.py `:60-68 _deny`/`:142 raise`；sql.py `:35-41,84-93 raise` | `test_viewer_get_stats_business_failure_ok_false`、`test_protocol_channel_tool_exception_not_silent_success`、`test_rest_manager_wraps_exception_as_ok_false` |
| 身份维度不完整（REST 缺 permissions；RAG Tool 缺 roles） | 绑定/解析只覆盖部分维度 | `routes/mcp.py:79-88`（五元组显式绑定）；`tools/rag.py:70-87`（roles 入 principal 与 ask_result） | `test_consecutive_requests_isolated`、`test_ask_result_receives_roles_and_principal`、`test_admin_vs_plain_user_kb_visibility` |
| Java 通道身份维度部分绑定（roles/tenant/permissions 可继承语义不明） | 只绑 user_id/department | `backend/app/api/routes/internal_ai.py`（五元组显式绑定，未携带维度置空） | `test_actor_delegation_with_explicit_blanks`、`test_tool_whitelist_denied` |

## 3. 安全验收

### 3.-2 终轮修复（最后一条已识别代码级阻断：RagAuthorizationError 污染路径）

| 阻断 | 修正 | 证据 |
|---|---|---|
| rag.py list_documents/get_stats 捕获 RagAuthorizationError 后经 str(e) → _deny → safe_message 透传；authz.build 包裹底层异常文本（authz.py:135 `f"RAG 授权计算失败: {exc}"`） | rag.py：str(e) 不再进 safe_message、不落日志——只记异常类型+位置（`_safe_error_location`）；固定安全拒绝文案（"RAG MCP 未认证或授权失败，已拒绝列出文档/统计"）+ `permission_denied` 错误码；fail-closed 语义不变；`_deny` 移除 detail 参数（消除未来注入点） | `test_rest_list_documents_fixed_denial`（poisoned build：error 恒等于固定文案、error_code=permission_denied、SECRET_KEY/内部路径/用户内容零出现）、`test_rest_get_stats_fixed_denial`、`test_protocol_authz_error_contained`（8091 同路径围堵）——RagAuthorizationError 携带模拟 SECRET_KEY/内部路径/用户内容时 REST 响应、8091 响应、日志三面零原文 |

### 3.-1 第四轮复核修正（异常类型白名单不可靠，MCPToolError 显式化）

| 复核阻断 | 修正 | 证据 |
|---|---|---|
| 异常类型（PermissionError/RuntimeError）不能保证 str(e) 无敏感数据，按类型白名单透传不可靠 | 引入显式受控异常 `MCPToolError`（manager.py:34，`safe_message`+`code` 双字段，str(e) 恒安全）；manager/protocol_app **只透传该类型**，其余一切异常（含 PermissionError/RuntimeError）一律映射稳定错误码+通用文案。错误码集：`permission_denied` / `tool_failed` / `service_unavailable` / `internal_error` / `internal:<类型>` / timeout / tool not found——REST ok:false 与 MCP isError:true 语义不变 | `test_runtime_error_with_secrets_contained_everywhere`（RuntimeError 带模拟密钥/内部路径/用户内容 → REST error 通用文案 + error_code=internal:RuntimeError，响应与日志零原文）、`test_permission_error_with_sensitive_content_contained`（同围堵）、`test_controlled_business_exception_passes_through`（MCPToolError 正常透传 safe_message+code） |
| 原始异常消息直接进入普通日志 | 全部异常日志路径重写：不再记录 str(e) 原文与 exc_info 堆栈文本；改记**异常类型 + 发生位置（file:line:func）+ trace_id**（`_safe_error_location`，manager.py:50）。受控 MCPToolError 记 safe_message（构造时声明安全）。rag/sql 治理失败只记枚举 status/error_code，`result.error_message`/`result.error`（可能含查询上下文）不透传、不落日志 | `test_internal_log_retains_diagnostics`（含 `_SENSITIVE not in caplog.text`）、`test_runtime_error_with_secrets_contained_everywhere`（日志断言）、`test_permission_error_with_sensitive_content_contained`（日志断言） |
| RAG search_knowledge 的 result.error_message 上抛路径 | rag.py:150-158：治理失败只记 `status`（枚举）与 `error_code`，上抛 `MCPToolError("search_knowledge 执行失败，请稍后重试", "tool_failed")`——error_message 不进 safe_message、不进日志 | 同上；search_knowledge 执行点测试回归通过 |

最终行号定位：manager.py:34（MCPToolError）/50（_safe_error_location）/124-150（异常映射）；rag.py:61-71（_deny）/150-158（治理失败）；sql.py:42-44,95-105（三处 raise）；protocol_app.py:119-131（_fn 映射）。

### 3.0 第三轮收口补充验收（异常泄漏 / Java 信任边界 / 失败路径）

| 验收项 | 结论 | 证据 |
|---|---|---|
| 异常敏感信息泄漏（REST） | PASS | 【已被第四轮修正取代，保留作审计轨迹】manager.route 异常分类：受控业务异常（PermissionError/RuntimeError，消息由代码构造）透传；意外异常只回通用文案 + `error_code=internal:<类型>` + trace_id。**复核发现类型白名单不可靠，最终态见 3.-1**。`test_rest_unexpected_exception_contained`（OSError 携带 `/srv/secret/path SECRET_KEY=sk-ant-123 用户敏感内容` → 响应零原文）、`test_controlled_business_exception_passes_through` |
| 异常敏感信息泄漏（标准 MCP 8091） | PASS | 【已被第四轮修正取代】protocol_app._fn 边界同策略：意外异常转受控文案 `[internal:<类型>]`，原文仅进服务器受控日志。**最终态见 3.-1（MCPToolError 显式类型）。**`test_protocol_unexpected_exception_contained`（db conn 异常含敏感串 → resp.text 零原文） |
| 异常堆栈与日志 | PASS（边界声明） | 【已被第四轮修正取代】原文与堆栈仅写入服务器受控日志。**复核指出普通日志也不应含原始异常消息；最终态=日志只记类型+位置+trace，见 3.-1。**`test_internal_log_retains_diagnostics` |
| Java /internal/ai/call 信任边界 | PASS（最小加固） | 核实：真实 Java 调用方尚未接入（AI_TOOLS_ENABLED 默认 false，无现网流量）；委托模型以 X-Internal-Token 为锚（token=可信内部服务，actor 由 Java 侧从已验证会话透传，与网关-后端委托模型一致）。最小修复：actor_user_id/actor_department 加 `max_length=128` + 禁控制字符 pattern（防 CRLF 审计日志注入）。`test_java_actor_injection_rejected`（CRLF 注入与超长均 ValidationError，合法 svc: 前缀通过） |
| 其他失败路径不包装成功 | PASS | 代码核查：rag server 全部失败分支 raise（治理失败/授权失败/执行失败/未知 tool）；sql server 全部失败分支 raise（unavailable/permission_denied/其他）；manager not-found/timeout/异常均 ok:false；8091 全部 isError；internal_ai 透传 ok:false。无业务失败包装为成功的残留路径 |

### 3.1 首轮+返修验收（基线 49f26f2）

| 验收项 | 结论 | 证据 |
|---|---|---|
| 未认证访问拒绝 | PASS | REST 三端点 401；8091 wrong key 401（`test_wrong_api_key_401`）；8091 无 key 且 ALLOW_UNAUTHENTICATED=false 时 fail-closed（既有中间件语义，protocol 测试 `test_no_config_rejected` 回归通过） |
| RBAC | PASS | viewer/editor get_stats 拒绝（PermissionError）；admin/super_admin 通过；机器主体 service_workflow 无 rag.admin（`test_service_principal_has_no_rag_admin`）；admin 权限点判定源 `ROLE_PERMISSION_CODES`（唯一权威，实测映射正确） |
| 资源归属（文档级） | PASS | permission_scope=restricted 文档对权限声明缺失的 viewer 不可见（visible_scopes 下推，`test_viewer_without_permissions_cannot_see_restricted`）；admin 可见；file_path 不出边界 |
| 跨租户 | 部分PASS / **部分 UNVERIFIED** | tenant ContextVar 五元组绑定与请求间隔离有测试（`test_consecutive_requests_isolated`，t1/t2 隔离）；但**真实 PostgreSQL 双租户数据的行级隔离未实测**（测试用 FakeRegistry 替身，未起真库）——行级结果断言 UNVERIFIED |
| get_stats 管理员权限及脱敏 | PASS | 匿名/非 admin 拒绝；admin 返回脱敏统计（`"/srv" not in str(result)`、无 path 键、未登记 kb 不进计数）；8091 机器主体拒绝且响应零统计数字（`test_get_stats_denied_never_leaks_stats`） |
| 日志参数泄漏 | PASS | params 原文（SECRET_MARK）、RAG 问题原文（SECRET_QUESTION_TEXT）、回答原文（ANSWER_TEXT）均断言不落日志；长度指纹保留 |
| Trace 参数泄漏 | **UNVERIFIED** | 本轮断言覆盖应用日志（caplog）；trace 落库面（trace_writer/llm_usage 的 span body 原文留存）未做专门断言——既有 redaction 机制（`backend/observability/redaction.py`）未改动，但未实测 |
| 其他 MCP 入口绕过 | PASS（静态核查） | 四入口核查：REST（本轮修复）、8091（API key+机器主体+server 层门）、Java internal_ai（token+白名单+server 层门）、consistency（只读 discover 不执行 tool）。测试未发现剩余匿名触达 `MCPServer.call_tool` 的路径 |

## 4. 混合 Tool 架构检查（双入口授权绕过）

**结论：未发现绕过路径；未将任何本地 Tool 迁移进 MCP。**

| 共享能力 | 本地入口 | MCP 入口 | 统一校验边界 |
|---|---|---|---|
| search_knowledge | `skills/rag/skill.py:23` → BaseSkill → `governance_runtime.execute`（skills/base.py:541） | rag server → `governance_runtime.execute_sync`（rag.py:132） | 同一治理链 + `search_knowledge_tool` 本体权限链（ContextVar→resolve_tool_principal→授权矩阵），双入口收敛于同一 tool 函数 |
| NL2SQL | `routes/sql.py` get_sql_agent + policy | sql server `agent.ask_struct(policy=...)` | 同一 SQLAgent 策略链（SQLPolicyGuard 权限门/表域/scope/审计），无身份 fail-closed |
| 文档列表/统计 | `routes/rag_documents.py`（`_require_authz`） | rag server（`_tool_authorization`） | 同一 `RagAuthorization.build` 裁决源 |

残留说明：`tools/labels.py`、`tools/__init__.py` 仅为注册/标签面，无执行路径；`e2e_demo.py` 与 `observability.py:664` 的 `MultiAgentSystem().ask` 是 RAG 域演示面（后者有 require_user_actor JWT 门），权限透传不足已记录为 RAG 域后续项（`docs/reports/2026-10-11-MCP-P0-tool-governance-gaps.md` P-3），不构成本轮 MCP 修复的绕过。

## 5. AGENTS.md 规范一致性

**实际读取的规范**（worktree 内现行版）：`AGENTS.md`（根）、`docs/development/tool-skill-guide.md`、`docs/auth/authorization-standard.md`、`docs/development/testing-guide.md`、`docs/development/cross-cutting-contracts.md`。

| 规范条款 | 结果 |
|---|---|
| tool-skill-guide L40「生产 Tool 执行必须经 Governance Runtime，不得通过 MCP 路径绕过」 | 符合（返修4 已接入 execute_sync） |
| tool-skill-guide L16/L25「MCP 不复制 Tool 业务实现；复用既有注册/鉴权/错误契约」 | 符合（零新增注册表/授权体系） |
| tool-skill-guide L42「身份只取服务端验证后的上下文」 | 符合（require_identity/机器主体/actor 委托） |
| tool-skill-guide L48「不伪造成功、不静默吞错」 | 符合（失败语义 raise） |
| authorization §3.1「API Key 是服务凭据不自动映射成用户」 | 符合（8091 机器主体） |
| authorization §3.4/§3.6/§3.7（资源隔离/缺身份拒绝/日志不泄原文） | 符合 |
| cross-cutting §4「不信任 X-API-Key 当用户凭证」 | 符合 |
| testing-guide T2 安全升级 + 交付记录 + 不删断言 | 符合（3 例断言语义收紧升级，无删除；UNVERIFIED 项如实标注） |
| 冲突项 | 无源码偏离现行规范项；1 条规范缺口建议（authorization-standard 资源矩阵补「MCP 机器主体」行）留批次 4，未擅自改规范 |

## 6. 测试证据（真实执行）

**终轮修复后最终执行（最终提交对应代码，110 例）**：

命令：`python -m pytest backend/tests/test_mcp_security.py backend/tests/test_mcp_returnfix_acceptance.py backend/tests/test_mcp_protocol.py backend/tests/test_registry_consistency.py backend/tests/infra/test_worker_runtime_bootstrap.py backend/tests/sql/test_sql_production_closure.py -q`

结果：`110 passed, 1 warning`；**退出码 0**；FAIL 0 / SKIP 0。终轮新增 5 例（RagAuthorizationError 污染路径 3 例 + 8091 RuntimeError/PermissionError 敏感变体参数化 2 例）。

**第三轮收口执行（103 例，基线证据）**：

命令：`python -m pytest backend/tests/test_mcp_security.py backend/tests/test_mcp_returnfix_acceptance.py backend/tests/test_mcp_protocol.py backend/tests/test_registry_consistency.py backend/tests/infra/test_worker_runtime_bootstrap.py backend/tests/sql/test_sql_production_closure.py -q`

结果：`103 passed, 1 warning in 71.80s`；**退出码 0**；FAIL 0 / SKIP 0。warning 为 PytestUnraisableExceptionWarning（既有环境噪音）。新增 5 例异常泄漏围堵（TestExceptionLeakContainment，返修验收文件 19→24 例）。

**收口前基线（HEAD=49f26f2）执行**：

命令：同上六套件。结果：`98 passed, 1 warning in 62.70s`；**退出码 0**。

**用例级明细**（定向 13 + 返修验收 19，`-v` 实跑 32 passed）：
- `test_mcp_security.py`：13 passed（三端点 401×3、认证路由×2、日志指纹、get_stats 匿名/非admin/脱敏/scope×4、list_documents 匿名/下推×2、search 执行点收敛）
- `test_mcp_returnfix_acceptance.py`：19 passed（REST 矩阵×7、请求隔离×1、8091 机器主体×4、Java×2、权限参数传递×3、失败语义×2）
- 既有回归：test_mcp_protocol 9、test_registry_consistency、test_worker_runtime_bootstrap、test_sql_production_closure（含 2 例失败语义升级断言）合计 66 passed

**未执行范围及原因**：
| 未执行 | 原因 |
|---|---|
| 真实 LLM/Provider 链路（rag-service 容器 + 向量库） | 本地未起容器栈；按 testing-guide 标记 NOT VERIFIED，生产部署前须实机验收 |
| 真实 PostgreSQL 双租户行级数据断言 | 测试环境用 registry 替身；真库 tenant 行级隔离属 AU-04/05 实测范围 |
| Docker 容器部署 E2E / 前端 registry 页面 401 实测 | 本轮为代码级整改+单测/接口级验证；容器与前端行为标注 UNVERIFIED |
| 全量 pytest 回归（约 20 分钟） | 按仓库纪律不默认全量；已跑受影响全部 6 套件 |

**历史执行**：返修前基线 79 条（13+66）全绿记录保留；返修后全部受影响测试重跑（98 passed），无 skip/xfail 掩盖。

## 7. 影响范围

| 维度 | 是否改变 | 说明 |
|---|---|---|
| Tool Schema | 否 | args_schema 派生（ADR-0001）未动；list_tools 参数定义不变（registry 一致性测试回归通过） |
| 执行契约 | 部分（有意收紧） | 失败形态：错误 dict → 协议级异常（REST ok:false / MCP isError:true）；3 处既有测试断言随之升级，调用方语义显式化 |
| 业务行为 | 部分（有意收紧） | 未认证 `/api/mcp/*` 401；8091 机器主体受限可见范围（无 rag.admin）；Java list_documents 授权下推；正常已登录用户与本地 Tool 路径行为不变（66 条回归佐证） |
| 数据库 | 否 | 零迁移、零 schema 变更 |
| 配置 | 否 | 无新配置项（复用既有 IDENTITY_SOURCE/API_KEY/AI_TOOLS_* 等） |
| 前端 | 否 | 零前端改动；cs/admin registry 页面已登录场景不受影响（静态分析，前端实测 UNVERIFIED） |

## 8. 遗留风险与后续待办

**已知问题/未覆盖路径（UNVERIFIED 汇总）**：
1. 真实容器/Provider 链路、真实双租户行级数据、Trace 落库脱敏、前端 401 行为——见第 6 节未执行范围（生产部署前须实机验收）。
2. MCP search_knowledge 的 session_id 共享 "default"（remote 形态记忆串扰面，非身份问题，待拍板）。
3. 8091 `sql_query` 机器主体的真实 SQL 执行未实测（策略链单测覆盖，真库执行 UNVERIFIED）。

**治理缺口登记**（本轮发现，均不导致本修复被绕过，详见 `docs/reports/2026-10-11-MCP-P0-tool-governance-gaps.md`）：
- Registry：R-1 无 spec 兜底路径（skills/base.py:524）、R-2 双注册表一致性无 CI 门、R-3 MCP 手写工具无 capability 对应物
- Executor：E-1 TOOL_RUNTIME_ENABLED fail-open（skills/base.py:813）、E-2 execute_sync loop 约束属约定非机制（guard.py:302）、E-3 预算绑定 fail-open（rag_server.py:306）
- 权限治理：P-1 list_tables 无资源级门、P-2 session 共享、P-3 observability ask 直调、P-4 静态白名单无分级
- 观测：O-1 REST MCP 无 trace span/治理事件、O-2 无结构化审计行、O-3 get_stats 访问无审计留痕

**阻塞项**：无。

**后续阶段待办**（按既定施工顺序，本轮不实施）：PR #24 规范修订重落地（批次 2）→ 最小 CI/评测门禁（批次 3）→ Tool/Prompt/预算公共治理（批次 4，含 P1-1/P1-2 spec 登记与 fail-open、管理中心 V1 前的 T0 审计以上述缺口清单为基线）→ 业务域与前端（批次 5）→ 历史清理（批次 6）。

---

## 总结论：**READY_FOR_REVIEW**（第四轮复核阻断已修复，最终态见 3.-1；本文件随最终提交更新，HEAD 以 PR 页面为准）

代码级 P0 修复完整、定向与回归测试全部真实通过（最终 105 passed / exit 0，历史 98/103 逐轮保留）、规范一致性无冲突；UNVERIFIED 项全部为环境依赖类（真库/容器/前端/Trace 落库），已逐条明示，不阻塞进入 GPT 独立复核。**未合并 main；未启动 PR #24 或 Tool Governance 施工。**
