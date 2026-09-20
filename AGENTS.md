# AGENTS.md

> 项目级约束与架构知识，随代码一起演进。个人偏好（语言/环境路径/工具）见全局 ~/.Codex/AGENTS.md
> 本文只留硬约束与索引；细节以 `docs/` 下对应文档为准。

## Project

电商 RAG + Multi-Agent 平台｜Backend: FastAPI + LangGraph｜Frontend: Next.js 14 + React
AI: DeepSeek（langchain-openai 兼容接口）
DB: PostgreSQL `agent_business`（业务仓库）+ `agent_memory`（元数据库）

## Architecture

```
POST /chat/stream → GraphRunner（Input Guard 门禁 → memory.start_session → graph.stream）
START → router ─┬─ CS 预过滤命中（灰度放量） ────────→ 客服域图 cs_graph_node → END
                ├─ 旅游预过滤命中（TRAVEL_ENABLED） → 旅游域图 travel_graph_node → END
                └─ 三层 Router（rule→vector→LLM）→ route_selector
                      ├─ direct   → skill_executor（跳过 Planner 直调 skill）→ reporter → END
                      ├─ workflow → workflow_executor → reporter → END
                      └─ plan     → planner → critique → supervisor（Send 并行）→ reporter → END
```

- 主图核心节点固定 8 个，顺序与命名不得随意改动（`builder.py`）；Skill 节点与域图节点由自动发现加入，**不得手写进 builder**。
- planner→critique→supervisor 是 plan 支线专属；direct/workflow/两个域图均绕过。预过滤优先级：客服 > 旅游（"订单里的行程单"属客服诉求）。
- 客服子图：state_loader → pending_handler → cs_supervisor（handoff 拦截/循环上限/LLM 兜底）→ 5 专家 → 回 supervisor → cs_reporter
- 旅游子图：travel_slot_filler → travel_supervisor（纯规则）→ poi/transit/budget/risk 专家 → travel_validator →（未通过）travel_repair → 回 supervisor → travel_reporter
- RAG 子链路：改写 → MultiQuery → 混合检索（向量+BM25）→ 同文档扩展 → Rerank → EvidenceGate → 带引用生成 → META 尾拒答判定
- 流式：节点 status/log + LLM stream_sink delta 汇入 merged_q；SSE 帧序 meta → status/log/delta → done/error

### 网关与异步层

- **APISIX(9080) 是唯一入口**；主链路 `/chat/stream` 同步执行、不经队列，SSE 直返。
- **Celery**（`backend/tasks/`，Redis 兼 broker/result backend）：双队列 `agent`｜`rag_index`（`celery_app.py::task_routes` 固定路由）；状态权威在 PG（agent_memory.tasks）；payload 仅 task_id、acks_late+prefetch=1、软/硬双层超时；重试 = 从最近 LangGraph checkpoint 自愈式续跑（业务终态异常不重试）。细节见 `docs/OPTIMIZATION_P3_ASYNC_QUEUE_ARCHITECTURE.md`。

### 节点职责与口径

- **Planner**：只做任务拆解 → Capability DAG，禁调 Tool/Skill/DB ｜ **Critique**：规则校验优先，仅 anomaly 调 LLM ｜ **Supervisor**：纯规则 DAG 调度，Send[] 并行 + 注入 previous_outputs ｜ **Skill**：业务封装不碰外部系统 ｜ **Tool**：无状态可测试 ｜ **Reporter**：step_results → Markdown
- 规模口径（2026-09-16）：12 Skill / 17 capability（3 内部 `routed:false`）/ 34 Tool / 4 workflow / 2 域图 / 主图 8 核心节点 / MCP 2 server 5 tool。勿把所有节点统称 Agent；权威口径与例外台账见 `docs/2026-09-16-Agent-Skill-Tool-MCP四层设计规范.md`。
- `routed: false` 只约束路由层，Planner/critique 仍遍历全量 17 个（`email.watch` 是 120s 阻塞长轮询，收紧属行为变更，台账 E9）。

### Capability DAG

```json
{"nodes": {"1": {"step_id": "1", "capability": "sql.query", "params": {"question": "..."}},
           "2": {"step_id": "2", "capability": "business.analyze", "params": {}}},
 "edges": {"2": ["1"]}}
```

17 个 capability 的 capability→Skill→节点名映射以 `capabilities.yaml` 为唯一事实源（G2，禁止手抄维护第二份表）。

### 新增资产规范（Tool / Skill / Workflow / MCP / Agent）

完整模板与隐藏接线点总表见 `docs/2026-09-16-新增Agent-Skill-Tool-MCP操作手册.md`。**规范 > 本摘要**。

| 加什么 | 改几处 | 关键动作 |
|---|---|---|
| Tool（原子操作） | 2 | `backend/tools/<域>/<mod>.py`：`@tool` + **文件底部** `tool_registry.register(my_tool, __file__)`；❌ 不得定义在 `skills/` |
| Skill + capability | 5 | ①skill.py ②`__init__.py` 自注册图节点 ③`skills/registry.py::_instances` ④`skills/__init__.py` re-export ⑤`capabilities.yaml` —— **漏⑤ = 永远路由不到** |
| Workflow | 3 | 类实现 + `workflows/__init__.py::register_all()` + `capabilities.yaml` 的 `workflows` 段（漏第三处 = 向量路由失明） |
| MCP Server | 2 | 继承 `MCPServer` + `servers/__init__.py::register_all()`；参数一律 `langchain_tool_to_mcp_meta` 从 `args_schema` 派生，**禁止手写** |
| 域图 / 业务 Agent | 5~7 / 2 | 手册 §6/§7；域图用技能 `agent-platform-add-domain-graph`（prefilter 必须插进 `router_node.py`，否则域永不触发） |

**铁律**：**G1** 声明式注册、启动期派生、fail-fast｜**G2** 单一事实源，派生量禁止手写回去｜**G3** 谁定义谁注册，禁止集中代注册｜**G4** 例外必须登记规范 §4 台账。
**方向**：`Planner → capability → Skill → Tool → Infrastructure`，上层调下层；MCP 不是第 5 层，是 Tool 的第二出口（Tool 不得 import Skill）。

**Skill 硬约束**：`name` = 目录名（节点名 `<name>_skill` 由其推导）；必填 `name/capabilities/description/examples/params_schema`（`__init_subclass__` 类定义期抛 TypeError）；capability 恰一个点 `<域>.<动作>` 全域唯一，workflow 纯蛇形不带点。❌ Skill 层定义 `@tool`、直接写 SQL/调 HTTP；多 Tool 覆写 `_select_tool()` 分发并把 params 裁到目标 Tool 签名内。
**新 Tool 三规**：`@tool`｜底部注册｜返回 JSON 字符串（失败返 `{"error":…}`，「查不到」与「查不了」分开），统一走 `tools/map/_base.py` 的 `ok/fail/not_configured`（存量 18 个返 Markdown 是例外 E8，别参照）。副作用 Tool 必须过 `security/tool_approval.ensure_approved()`；user_id 取 `tools/session.get_tool_user_id()`，禁止硬编码。
**易漏接线**：新 capability 加 `direct_executor.py::_USER_CAP_LABELS`。

**验证（改完必跑）**：`cd backend && "$PY" -m pytest tests/test_registry_consistency.py tests/test_layer_consistency.py tests/test_adr0001_dual_registry_merge.py -q --no-cov`
⚠️ 局部跑**必须加 `--no-cov`**（`pytest.ini` 挂死 `--cov-fail-under=55` 且无运行范围隔离 → 用例全绿但 EXIT=1，并覆写项目级覆盖率产物）；改 `params_schema`/描述/prompt 后另跑 planner 评估（`datasets/planner_params.json`）。

### SQL 子系统与数据库

`SQLSkill → SQLAgent → Router → Generator → Validator(6层) → RowSecurity → Executor(连接池) → PostgreSQL`
6 层安全：①SELECT 校验 ②表名白名单 ③敏感列拒绝 ④函数黑名单 ⑤LIMIT 强制 ⑥agent_readonly 只读角色。数据协议 SQLResult / BusinessInsight，步骤间 Supervisor 注入 `previous_outputs`。
库：7 schema × 18 表（product/order/inventory/customer/crawler/finance/ai）；连接池 min=2 max=10；Migration 走 `sql/migrations/`。

### 旅游规划域图（`backend/travel/`，P0）

接入与客服域一致：`travel/register.py` 自注册 → `domains/__init__.py` 触发 → builder 自动布线，**不改 builder.py**。契约（Pydantic）：`TravelBrief → Poi → Itinerary`；状态只存 dict（`load_*/save_*`），保证 checkpointer 可序列化。
**validator = 旅游域的 Evidence Gate**：纯规则零 LLM 零 IO；只判定不修改（修复在 `repair.py`）；error 阻塞交付并触发修复；四轴 = 时间/地理/体力/预算。**局部修复**只动被点名的天与条目，用户点名必去条目**永不被静默丢弃**（kept_required）。
`travel.plan` 不注册主图 Skill（有状态多步流程已由域图承担）；只注册无状态的 `travel.poi_search`。数据源 P0 本地种子（坐标为示例值），P1 换地图/票务 MCP 契约不变。开关 `TRAVEL_ENABLED`，阈值集中 `config/travel.py`。

**跨轮契约（checkpointer 关闭时也须遵守）**
1. `new_travel_graph_input()` **只放本轮输入**，不预置产物/执行态默认值——checkpointer 把 input 当对上轮状态的**更新**合并，预置 `brief: {}` 等于每轮清空成果
2. 读状态一律 `.get()`——本轮没写过的键不在最终状态里
3. `brief_fingerprint` 变 → 只在 slot_filler 里 `planning_reset()`；不清则 supervisor 会把**上一轮行程**当新需求输出

**checkpointer**：三处 `_build_checkpointer`（主图/客服/旅游）均 postgres 优先、失败降级 MemorySaver；需 psycopg **v3** + `langgraph-checkpoint-postgres`（依赖已在 pyproject.toml 与 requirements-lock.txt 声明，本地 venv 已补齐）；`config/startup.py` 只探测 import 不探测连通性，缺驱动时 warning 点名。
两个锁文件坑（**照旧装会失败**）：① `langgraph-checkpoint` 原钉 4.0.3 与 `-postgres==3.1.0` 要求的 >=4.1.0 冲突 → 已升 **4.2.0**；② Windows/无 libpq 必须装 `psycopg[binary]`，否则 `no pq wrapper available`。
TTL 清理收敛 `orchestration/graph/checkpointer_cleanup.py`（全进程单例，改 TTL 三处一起改）。

## Design Principle

必须满足: 可理解、可测试、可观测、可维护、可扩展、可控制、可靠性
禁止: Demo 跑通式开发、临时堆叠、`except Exception: pass`
Priority：P0 数据错误/安全/崩溃/Trace 丢失 ｜ P1 架构/强耦合/重复代码 ｜ P2 命名/注释

## Code Rules

- Python: snake_case、类型注解、logger 替代 print、具体异常、SQL 参数化
- 禁止: 业务代码直接 os.getenv、文件名 misc/helper/common/utils2 ｜ Tool 必须独立可测试
- **写操作审批门**：副作用工具执行前必须 `security/tool_approval.ensure_approved()`（TOOL_APPROVAL_MODE=required 默认建审批单，管理员经 `/api/approvals` 批准后同指纹放行）；表 `ai.tool_approval_requests`（migration 007）
- **工具契约兼容**：契约与消费方同仓同发布，不加版本号；只向后兼容演进（新增可选参数），破坏性变更 = 新 capability + 旧能力废弃期；契约回归门 = registry/layer/adr0001/base_output 四个一致性测试 + e2e 故障注入（`datasets/e2e/cases.jsonl` F-*）；改 params_schema/prompt 后跑 planner 评估
- **主图保护**：recursion_limit = MAIN_GRAPH_RECURSION_LIMIT（默认 80）；checkpointer 默认关（MAIN_GRAPH_CHECKPOINTER_ENABLED），开启后 request_context 以 `checkpoint_safe()` dict 进状态，thread_id 每轮唯一

## Change Flow

明确目标 → 阅读代码 → 分析影响 → 修改 → 测试 ｜ Bug 先复现、Refactor 测试通过、Feature 优先补测试
编写/修改任何测试前必须遵循铁律（强断言、只 mock 外部边界、必须运行、假阳性自审）；存量测试随 diff 增量审查，不主动全量翻修。

## Validation

Backend: `py_compile` + `pytest tests/sql/ -v` ｜ Frontend: `npx tsc --noEmit` + `npm test` ｜ E2E: `cd backend && python e2e_demo.py`
管理端对账：`GET /api/agents`、`GET /api/capabilities`
文档索引 `docs/README.md`；四层规范与新增资产手册见上文链接；记忆 用户级 `~/.Codex/projects/<project>/memory/MEMORY.md`

## 服务启停与网关边界

**唯一启停入口 = `devctl.bat` 系列**（旧的 start_py/start_frontend 等 .bat 已删除）。完整实测踩坑清单见 `命令文档.md` 与 `docs/gateway-apisix-final-report.md`。

```bash
.\devctl.bat status                          # 空参 = status
.\devctl.bat start|stop|restart [backend|admin|web|all] [/y]
.\devctl.bat all /y                          # stop+start 全量
# 短路入口 dev-start/dev-stop/dev-restart.bat 等价，底层实现 dev-svc.bat（一般不直接调）
```

- 服务：`backend` = docker compose `app`（:8000）｜`admin` = frontend-admin（:3200）｜`web` = frontend（:3100）｜网关 APISIX :9080
- **`backend` 只按服务名操作**：`stop backend` 只停 app 容器，不动 postgres/redis/apisix/rag-service/mcp-service/worker；整套栈用 `docker compose up -d` / `down`
- **脚本化/agent 调用一律加 `/y`**（stop/restart 确认是交互式，否则挂住）；仅支持 cmd/powershell——Git Bash 用 `/c/Windows/System32/cmd.exe /c "devctl.bat status"`，且当前目录须已是仓库根
- 前端每次 start 都新开一个空 `NEXT_DIST_DIR=.next-dev-<rand>`（复用非空 distDir 必启动失败），故 `frontend/.next-dev-*`、`frontend-admin/.next-dev-*` 会不断堆积（实测 16 个目录 ~300MB，`.gitignore` 用 `.next-*/` 兜住）；**脚本不清理旧 distDir，需手动删**
- 宿主机 `127.0.0.1:8000` 可能有 Docker 残留僵尸绑定 → 裸跑 uvicorn 前先重启 Docker Desktop

**已知坑（实测，详见 `命令文档.md`、`docs/gateway-apisix-final-report.md`）**：

- **.bat 必须 ASCII-only**（cmd 按 GBK 解析，中文注释会破坏控制流）；**别用 `timeout /t`**（Git Bash PATH 会解析到 GNU timeout，改用 `ping -n N 127.0.0.1 >nul`）
- **`dev-svc.bat` 未跟踪**（`?? ` 状态）：它是 `devctl`/`dev-{start,stop,restart}.bat` 四者的共享实现，删旧脚本时务必一起 `git add`，否则新提交的入口脚本会指向一个不存在的文件
- app 容器换 IP 后 APISIX 有 ~1-2min 502 窗口（`dns_resolver_valid: 5` 已缓解），急用 `docker compose restart apisix`；oa-auth-service/system 无重启策略，引擎重启后需手动 `docker start`
- oa-auth 整栈曾被反复 SIGKILL(137)：修复 = `docker start oa-auth-nacos oa-auth-mysql oa-auth-redis oa-auth-service oa-auth-system` → `docker network connect agent_agent-net <容器>` → 重启前端。vpnkit 回环不可靠，**容器名直连是首选**；本机 5432 是宿主机原生 PG，不是 agent-postgres
- 杀端口脚本都带 docker 守卫（容器占端口时跳过，防误杀 com.docker.backend）
- **py 与 Java 是两个独立项目**：Java = Enterprise_OA（备份 `.workbuddy/java-legacy-backup/`，割接清单 `docs/java-side-handover.md`）；唯一联系是 Java 客服调 py agent
- **认证 py 自建**（issuer=agent-platform，`backend/security/local_jwt.py`，migration 008）；APISIX `gateway-auth` 插件验 JWT——**Bearer 优先于 X-API-Key**，注入 X-User-Id 身份头（契约 `docs/contracts/identity-header-protocol.md`）；登录链路前端 `/login` → APISIX `/api/auth/**` → auth-service，回滚 = env 改回 8080
