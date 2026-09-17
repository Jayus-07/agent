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
- planner→critique→supervisor 是 plan 支线专属；direct/workflow/两个域图均绕过。
- 客服子图：cs_state_loader → cs_pending_handler → cs_supervisor（handoff 拦截/循环上限/LLM 兜底）→ 5 专家 → 回 supervisor → cs_reporter
- 旅游子图：travel_slot_filler → travel_supervisor（纯规则）→ poi/transit/budget/risk 专家 → travel_validator →（未通过）travel_repair → 回 supervisor → travel_reporter
- 预过滤优先级：客服 > 旅游（"订单里的行程单"属客服诉求）
- RAG 子链路：改写 → MultiQuery → 混合检索（向量+BM25）→ 同文档扩展 → Rerank → EvidenceGate → 带引用生成 → META 尾拒答判定
- 流式：节点 status/log + LLM stream_sink delta 汇入 merged_q；SSE 帧序 meta → status/log/delta → done/error

### 入口与异步层（网关 + 队列）

- **网关**：APISIX(9080) 是 Python 项目唯一入口（详见「服务启停与网关边界」）；**主链路 `/chat/stream` 同步执行、不经任何队列**，SSE 直返。
- **Celery 任务队列**（`backend/tasks/`，Redis 兼作 broker 与 result backend，与业务缓存分库默认 /1）：
  - 双队列固定路由（`celery_app.py::task_routes`）：`agent`（execute_agent，通用 Agent 任务）｜ `rag_index`（execute_index，RAG 上传索引——吃内存/模型，与 agent 隔离扩缩容）；入口 `POST /rag/upload` → `apply_async`；**状态权威在 PG（agent_memory.tasks）**，result backend 24h 过期仅供查询
  - 可靠性：payload 仅 `task_id`（json 禁 pickle）｜ acks_late + prefetch=1 + reject_on_worker_lost（Worker 宕机回队）｜ 软/硬双层超时 ｜ 重试 = **自愈式续跑**（从最近 LangGraph checkpoint 继续，已完成节点不重跑；业务终态异常不重试）
  - 部署：docker-compose `worker` 服务（`backend.workers.agent_worker`，`-Q agent,rag_index`，inspect ping 健康检查）；指标走 celery-exporter（celery_task_{sent,succeeded,failed,retried}_total）

### 节点职责

- **Planner**：只做任务拆解 → Capability DAG（nodes+edges），禁止调用 Tool/Skill/DB
- **Critique**：审查修正计划（规则校验 0ms 优先，仅 anomaly 时调 LLM）
- **Supervisor**：按 edges 依赖顺序调度，Send[] 并行派发，自动注入 previous_outputs
- **Skill**：业务能力封装，不直接访问外部系统 ｜ **Tool**：无状态、可测试 ｜ **Reporter**：step_results → Markdown

### Agent 口径

描述系统规模时区分节点类型，勿把所有节点统称 Agent：
- **LLM 决策节点（3）**：planner / critique（仅 anomaly 时）/ reporter
- **混合路由点（2）**：router（rule→vector→llm 三层兜底）、cs_supervisor（规则优先+LLM 兜底）
- **规则/执行节点**：主图 supervisor 是纯规则 DAG 调度器（不调 LLM）；客服 5 专家仅 knowledge 走 LLM（经 RAG），query/action/complaint/handoff 为业务服务编排
- **工具节点（12 Skill / 17 capability）**：skills/registry.py 注册的 Skill，非决策 agent；LangChain tool 见 backend/tools/（含 memory_search/memory_store/calculate）

### 当前规模（2026-09-16）

12 Skill / 17 capability（3 个 `routed:false` 内部）/ 34 Tool / 4 workflow / 2 域图（客服 9 节点、旅游 9 节点）/ 主图 8 核心节点 / 内置 MCP 2 server 5 tool。
权威口径与逐条规则见 `docs/2026-09-16-Agent-Skill-Tool-MCP四层设计规范.md`（§2 规模、§3 分层规范、§4 例外台账、§6 守护测试矩阵）。
**`routed: false` 只约束路由层**（rule/vector/llm 三个 router 均排除），Planner prompt 与 critique 仍遍历全量 17 个 —— 即 `email.watch`（默认 `timeout_sec=120` 的阻塞长轮询）会与 14 个公开能力并列出现在 Planner 可选清单里，收紧属行为变更须单独评估（台账 E9）。

### Capability DAG

```json
{"nodes": {"1": {"step_id": "1", "capability": "sql.query", "params": {"question": "..."}},
           "2": {"step_id": "2", "capability": "business.analyze", "params": {}}},
 "edges": {"2": ["1"]}}
```

### 已注册 Capability（17 个 = 14 routed + 3 内部）

| capability | Skill | 节点名 |
|---|---|---|
| sql.query | SQLSkill | sql_skill |
| rag.search | RAGSkill | rag_skill |
| business.analyze | BusinessAnalysisSkill | business_analysis_skill |
| report.generate | ReportSkill | report_skill |
| email.send / email.search / email.read | EmailSkill | email_skill |
| email.watch（内部，routed:false） | EmailSkill | email_skill |
| data.export | DataExportSkill | data_export_skill |
| web.search | WebSearchSkill | web_search_skill |
| web.crawl | WebCrawlSkill | web_crawl_skill |
| data.collect | DataCollectionSkill | data_collection_skill |
| travel.poi_search | TravelPoiSkill | travel_poi_skill |
| map.lookup | MapLookupSkill | map_lookup_skill |
| competitor.analyze | CompetitorAnalysisSkill | competitor_analysis_skill |
| competitor.watch / .history（内部，routed:false） | CompetitorAnalysisSkill | competitor_analysis_skill |

### 新增资产规范（Tool / Skill / Workflow / MCP / Agent）

完整模板与隐藏接线点总表见 `docs/2026-09-16-新增Agent-Skill-Tool-MCP操作手册.md`（分层规范与其 §4 例外台账见四层设计规范）。**规范 > 本摘要**。

| 加什么 | 改几处 | 关键动作 |
|---|---|---|
| Tool（原子操作） | 2 | `backend/tools/<域>/<mod>.py`：`@tool` + **文件底部** `tool_registry.register(my_tool, __file__)`；❌ 不得定义在 `skills/`（`TestToolLayerOwnership` 会抓） |
| Skill + capability | 5 | ①`skills/<name>/skill.py` ②`skills/<name>/__init__.py` **自注册图节点** ③`skills/registry.py::_instances` ④`skills/__init__.py` re-export ⑤`capabilities.yaml` 加条目 —— **漏⑤ = 用户提问永远路由不到** |
| Workflow | 3 | 类实现 + `workflows/__init__.py::register_all()` + `capabilities.yaml` 的 `workflows` 段（**漏第三处 = 向量路由对它失明**） |
| MCP Server | 2 | `mcp_servers/servers/<name>.py` 继承 `MCPServer` + `servers/__init__.py::register_all()`；参数一律 `langchain_tool_to_mcp_meta(tool, …)` 从 `args_schema` 派生，**禁止手写** |
| 域图 / 业务 Agent | 5~7 / 2 | 手册 §6/§7；域图用技能 `agent-platform-add-domain-graph`（prefilter 必须插进 `router_node.py`，否则域永远不触发） |

**铁律**：**G1** 声明式注册、启动期派生、fail-fast（不静默降级）｜**G2** 单一事实源，派生量**禁止手写回去**（capability 唯一事实源 = `capabilities.yaml`）｜**G3** 谁定义谁注册，**禁止集中代注册**｜**G4** 例外必须在规范 §4 台账登记。
**方向**：`Planner → capability → Skill → Tool → Infrastructure`，上层调下层、下层不知道上层存在；MCP 不是第 5 层，是 Tool 的第二个出口（Tool 不得 import Skill）。

**Skill 硬约束**：`name` 须与目录名一致（决定节点名 `<name>_skill`，`__init__.py` 里注册的字符串必须由 `name` 推导）；必填 `name`/`capabilities`/`description`/`examples`/`params_schema`（`__init_subclass__` 类定义期抛 `TypeError`）；capability `^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$`（**恰一个点** `<域>.<动作>`）全域唯一，workflow 纯蛇形**不带点**（`manifest.py` fail-fast）。
**Skill 层禁止**：❌ 定义 `@tool`（Tool 一律放 `backend/tools/`）❌ 直接写 SQL / 调 HTTP。多 Tool 时覆写 `_select_tool(capability, params)` 分发，并把 params 裁到目标 Tool 签名内（LangChain `invoke` 遇未知参数直接抛错）。

**新 Tool 三规**：`@tool` ｜ 底部注册 ｜ **返回 JSON 字符串**（失败返 `{"error": …}`，**「查不到」与「查不了」必须分开**），统一走 `tools/map/_base.py` 的 `ok/fail/not_configured`（存量 18 个返 Markdown 是登记例外 E8，别参照也别改造）。写副作用 Tool 必须过 `security/tool_approval.ensure_approved()`；user_id 取 `tools/session.get_tool_user_id()`，禁止硬编码。
**易漏接线**：新 capability 要加 `direct_executor.py::_USER_CAP_LABELS`（否则用户看到标题「信息查询」）。

**验证（改完必跑）**：`cd backend && "$PY" -m pytest tests/test_registry_consistency.py tests/test_layer_consistency.py tests/test_adr0001_dual_registry_merge.py -q --no-cov`
⚠️ 局部跑**必须加 `--no-cov`**（`pytest.ini` 挂死 `--cov-fail-under=55` 且无运行范围隔离 → 用例全绿但 `EXIT=1`，并覆写项目级覆盖率产物）；改 `params_schema`/描述/prompt 后另跑 planner 评估（`datasets/planner_params.json`）。

### SQL 子系统

`SQLSkill → SQLAgent → Router → Generator → Validator(6层) → RowSecurity → Executor(连接池) → PostgreSQL`
6 层安全：①SELECT 类型校验 ②表名白名单 ③敏感列拒绝 ④禁止函数黑名单 ⑤LIMIT 强制 ⑥agent_readonly 只读角色
数据协议：**SQLResult**（sql/tables/columns/rows/row_count/execution_time，Skill 层输出）·
**BusinessInsight**（summary/risks/suggestions/confidence，BusinessAnalyzer 输出）；步骤间由 Supervisor 在 Send 注入 `previous_outputs`

### 数据库

7 schema × 18 表：product / order / inventory / customer / crawler / finance / ai
连接池 ThreadedConnectionPool（min=2, max=10）｜只读账号 agent_readonly（scram-sha-256）｜Migration: `sql/migrations/001~005`

### 旅游规划域图（`backend/travel/`，P0）

独立域图，接入方式与 `customer_service/` 一致：`travel/register.py` 自注册 → `backend/domains/__init__.py` 触发 → builder 自动布线，**不改 builder.py**。
契约（Pydantic）：`TravelBrief` → `Poi` → `Itinerary`（含 warnings/sources/confidence）；状态里只存 dict（`load_*/save_*` 转换），保证 checkpointer 可序列化。

**validator 是旅游域的 Evidence Gate**（RAG 的 Gate 管「没有依据就别答」，它管「行程物理上成不成立」）：
① 纯规则、零 LLM、零 IO ② 只判定不修改（修复在 `repair.py`，独立演进独立单测）
③ error 阻塞交付并触发修复，warning 只提示 ④ 四轴：时间（营业时段/闭馆日/重叠/长等候）· 地理（长通勤/在途总量/重复到访）· 体力（单日 POI 数与**纯到访**时长，不含通勤用餐）· 预算（超支/逼近上限）

**局部修复**（`repair.py`）：只处理被点名的天与条目，不整条重规划；用户点名必去的条目**永不被静默丢弃**，违规时保留并记为 kept_required。
**travel.plan 不注册成主图 Skill**：行程生成是有状态多步流程（槽位追问→骨架→排程→校验→修复），已由域图承担，再包 Skill 会有第二套实现且丢掉校验安全网；POI 检索是无状态单点能力，故注册 `travel.poi_search`。
数据源：P0 本地种子数据（`tools/travel/poi_seed.py`，source=`seed:local`，坐标为**示例值**非权威），P1 换地图/票务 MCP，契约不变。开关 `TRAVEL_ENABLED`（默认 false，同 CS_ENABLED 策略），阈值集中在 `config/travel.py`，不散落魔数。

**跨轮契约（checkpointer 关闭时也须一直遵守）**
1. `new_travel_graph_input()` **只放本轮输入**，不得预置产物/执行态默认值——checkpointer 把 input 当对上一轮状态的**更新**合并，预置 `brief: {} / itinerary: None / expert_history: []` 等于每轮清空成果
2. 读状态一律 `.get()`——「本轮没写过的键」不会出现在最终状态里
3. `brief_fingerprint` 变 → `planning_reset()` 清空规划产物重排，只在 slot_filler 里做；不清则 supervisor 会跳过专家直进 reporter，把**上一轮行程**当新需求输出

**checkpointer（2026-09-14 已打通）**：三处 `_build_checkpointer`（主图/客服域/旅游域）均为「postgres 优先、失败降级 MemorySaver」；需 psycopg **v3** + `langgraph-checkpoint-postgres`（依赖已在 pyproject.toml 与 requirements-lock.txt 声明，本地 venv 已补齐）；`config/startup.py` 只探测 import 不探测连通性，缺驱动时 warning 点名。
两个锁文件坑（**照旧装会失败**）：① `langgraph-checkpoint` 原钉 4.0.3 与 `-postgres==3.1.0` 要求的 >=4.1.0 冲突 → 已升 **4.2.0**；② Windows/无 libpq 必须装 `psycopg[binary]`，否则 `no pq wrapper available`。
TTL 清理收敛到 `orchestration/graph/checkpointer_cleanup.py`（三方共用同一组表，**全进程单例**；`customer_service/` 下为兼容薄壳）。改 TTL 三处一起改。

## Design Principle

必须满足: 可理解、可测试、可观测、可维护、可扩展、可控制、可靠性
禁止: Demo 跑通式开发、临时堆叠、`except Exception: pass`
Priority：P0 数据错误/安全问题/崩溃/Trace 丢失 ｜ P1 架构问题/强耦合/重复代码 ｜ P2 命名/注释
DB 生产标准：P0 只读角色 + scram-sha-256 + 连接池 + connect_timeout/keepalives ｜ P1 外键完整 + CHECK 约束 + 高频列索引

## Code Rules

- Python: snake_case、类型注解、logger 替代 print、具体异常、SQL 参数化
- 禁止: 业务代码直接 os.getenv、文件名 misc/helper/common/utils2 ｜ Tool 必须独立可测试

### 写操作审批门（human-in-the-loop）

写副作用工具（send_email/export_csv/data_collection/competitor 写动作）执行前必须调用
`security/tool_approval.ensure_approved()`：TOOL_APPROVAL_MODE=required（默认）时建审批单返回待批提示，
管理员经 `/api/approvals` 批准后重试同指纹操作放行（TTL 内）。表 `ai.tool_approval_requests`（migration 007）。
工具层身份来自 `tools/session.get_tool_user_id()`（RequestContext.bind 注入），禁止硬编码 user_id。

### 工具契约兼容规则（代替 per-schema 版本号）

契约（params_schema、output_type、capability 名）与消费方（Planner、Reporter）同仓同发布，schema 变更与消费方适配原子提交，**不加版本号**。兼容靠三条：
1. **向后兼容演进**：只新增可选参数；不改既有参数语义；不删/改名已有字段。破坏性变更 = 新 capability 名 + 旧 Capability 保留废弃期
2. **测试守护**：`test_registry_consistency.py`（能力派生/manifest 对账/Skill-Tool 对齐/MCP 派生）+ `test_layer_consistency.py`（@tool 必须已注册、@tool 不得定义在 skills/ 下、Skill 节点不得集中代注册、workflow↔manifest 对账、命名约定）+ `test_adr0001_dual_registry_merge.py` + `test_base_output_contract.py` + e2e 离线故障注入集（`datasets/e2e/cases.jsonl` F-* 用例）构成契约回归门
3. **变更跑评估**：改 params_schema/描述/prompt 后跑 planner 评估（live），关键写操作参数用 `datasets/planner_params.json` 的 expected.params 断言

仅当工具以独立部署制品对外（MCP Server 发布、跨团队共享）才引入显式版本号。

### 主图 LangGraph 保护

- recursion_limit: MAIN_GRAPH_RECURSION_LIMIT（默认 80），runner 每次 stream 传入
- checkpointer: MAIN_GRAPH_CHECKPOINTER_ENABLED（默认关）；开启后 request_context 以 `checkpoint_safe()` dict 进状态（trace/sink 不序列化），thread_id 每轮唯一

## Change Flow

明确目标 → 阅读代码 → 分析影响 → 修改 → 测试 ｜ Bug 先复现、Refactor 测试通过、Feature 优先补测试
编写或修改任何测试（含 bug 回归、补全存量）前，必须先加载 `test-quality-guard` skill 并遵循其铁律（强断言、只 mock 外部边界、必须运行、假阳性自审）；存量测试不主动全量翻修，随 diff 增量审查。

## Validation

Backend: `py_compile` + `pytest tests/sql/ -v` ｜ Frontend: `npx tsc --noEmit` + `npm test` ｜ E2E: `cd backend && python e2e_demo.py`
管理端只读总览（B13）：`GET /api/agents`、`GET /api/capabilities`（对账 Skill/Capability 台账）
文档索引 `docs/README.md`；四层规范 `docs/2026-09-16-Agent-Skill-Tool-MCP四层设计规范.md`；新增资产手册 `docs/2026-09-16-新增Agent-Skill-Tool-MCP操作手册.md`；记忆 用户级 `~/.Codex/projects/<project>/memory/MEMORY.md`

## 服务启停与网关边界（2026-09-15 APISIX 迁移后）

```bash
# Python 后端 :8000 —— 默认容器形态（agent-app-1，compose env 完整）
start_py.bat / stop_py.bat / restart_py.bat
#   native 参数 = 宿主机裸跑 uvicorn --reload（仅临时调试：需同时把
#   apisix/apisix.yaml 的 app 节点改回 host.docker.internal:8000）
# Java 服务（原生 mvn 热加载：auth-service :8006 / system-service :8002 / api-gateway :8080；
#   business-service 留容器；mysql/redis/nacos/postgres 基础设施容器不动）
start_java.bat / stop_java.bat / restart_java.bat
#   改了 Java 源码 → build_java.bat → restart_java.bat
# 前端 :3100（start 脚本默认注入 AUTH_GATEWAY_URL=http://127.0.0.1:9080）
start_frontend.bat / stop_frontend.bat
```

- **py 与 Java 是两个独立项目**：py = 本仓库；Java = Enterprise_OA（源码已移出，备份 `.workbuddy/java-legacy-backup/`，割接清单 `docs/java-side-handover.md`）。唯一联系：Java 客服系统调 py agent（`/internal/ai/call` + chat API）。
- 认证已 py 自建（issuer=agent-platform，`backend/security/local_jwt.py` + `routes/auth_local.py` + migration 008），不再依赖 Java auth-service。
- **APISIX(9080) 是 Python 项目唯一入口**（声明式 `apisix/apisix.yaml` 进 git + 自研插件 `apisix/plugins/gateway-auth.lua`）；Java SCG 归 Java 项目。默认容器集：postgres/redis/rag-service/mcp-service/app/apisix + **worker/celery-exporter（Celery 异步任务）**。
- 认证模型：`gateway-auth`（enforce）验 py 签发 JWT —— **Bearer 优先于 X-API-Key**，带 Bearer 必须走完整 JWT 流防绕过黑名单；+ Redis 黑名单（只读 agent-redis:6379）+ 注入 X-User-Id 等身份头；仅 X-API-Key 无 Bearer 走服务级透传。契约 `docs/contracts/identity-header-protocol.md`
- 登录链路：前端 `/login` → APISIX `/api/auth/**`（白名单）→ auth-service(JWT)；refresh_token 走 HttpOnly Cookie。回滚 = env 改回 8080 重启前端（SCG 与 Java 服务从未被修改）。

### 启停已知坑（实测，详见 `命令文档.md`、`docs/gateway-apisix-final-report.md`）

- **.bat 必须 ASCII-only**（cmd 按 GBK 解析，中文注释会破坏控制流）；**别用 `timeout /t`**（Git Bash PATH 会解析到 GNU timeout，改用 `ping -n N 127.0.0.1 >nul`）
- 宿主机 `127.0.0.1:8000` 有 Docker 残留僵尸绑定 → 裸跑 uvicorn 前先重启 Docker Desktop
- app 容器换 IP 后 APISIX 有 ~1-2min 502 窗口（`dns_resolver_valid: 5` 已缓解），急用 `docker compose restart apisix`；oa-auth-service/system 无重启策略，引擎重启后需手动 `docker start`
- oa-auth 整栈曾被反复 SIGKILL(137)：修复 = `docker start oa-auth-nacos oa-auth-mysql oa-auth-redis oa-auth-service oa-auth-system` → `docker network connect agent_agent-net <容器>` → 重启前端。vpnkit 回环不可靠，**容器名直连是首选**；本机 5432 是宿主机原生 PG，不是 agent-postgres
- 杀端口脚本都带 docker 守卫（容器占端口时跳过，防误杀 com.docker.backend）
