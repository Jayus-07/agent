# CLAUDE.md

> 项目级约束与架构知识，随代码一起演进。
> 个人偏好（语言/环境路径/工具）见全局 ~/.claude/CLAUDE.md

## Project

电商 RAG + Multi-Agent 平台

Stack:
- Backend: FastAPI + LangGraph
- Frontend: Next.js 14 + React
- AI: DeepSeek（langchain-openai 兼容接口）
- DB: PostgreSQL agent_business（业务仓库）+ agent_memory（元数据库）

## Architecture

```
POST /chat/stream → GraphRunner（Input Guard 门禁 → memory.start_session → graph.stream）
START → router ─┬─ CS 预过滤命中（灰度放量） ──────────→ 客服域图 cs_graph_node → END
                ├─ 旅游预过滤命中（TRAVEL_ENABLED） ─→ 旅游域图 travel_graph_node → END
                └─ 三层 Router（rule→vector→LLM）→ route_selector
                      ├─ direct  → skill_executor（跳过 Planner 直调 skill）→ reporter → END
                      ├─ workflow → workflow_executor → reporter → END
                      └─ plan    → planner → critique → supervisor（Send 并行）→ reporter → END
```

> planner→critique→supervisor 只是 plan 模式支线；direct/workflow/客服域图/旅游域图均绕过它。
> 客服子图：cs_state_loader → cs_pending_handler → cs_supervisor（handoff 拦截/循环上限/LLM 兜底）→ 5 专家 → 回 supervisor → cs_reporter。
> 旅游子图：travel_slot_filler → travel_supervisor（纯规则）→ poi/transit/budget/risk 专家 → travel_validator →（未通过）travel_repair → 回 supervisor → travel_reporter。
> 预过滤优先级：客服 > 旅游（"订单里的行程单"属客服诉求）。
> RAG 子链路：改写 → MultiQuery → 混合检索（向量+BM25）→ 同文档扩展 → Rerank → EvidenceGate → 带引用生成 → META 尾拒答判定。
> 流式：节点 status/log + LLM stream_sink delta 汇入 merged_q；SSE 帧序 meta → status/log/delta → done/error。

### 节点职责

- **Planner**: 只负责任务拆解 → 输出 Capability DAG（nodes + edges），禁止调用 Tool/Skill/DB
- **Critique**: 审查修正计划（规则校验 0ms 优先，仅 anomaly 时调 LLM）
- **Supervisor**: 按 edges 依赖顺序调度，通过 Send[] 并行派发，自动注入 previous_outputs
- **Skill**: 业务能力封装，不直接访问外部系统
- **Reporter**: 汇总 step_results → Markdown
- **Tool**: 无状态、可测试

### Agent 口径

描述系统规模时区分节点类型，勿把所有节点统称 Agent：
- **LLM 决策节点（3）**: planner / critique（仅 anomaly 时）/ reporter
- **混合路由点（2）**: router（rule→vector→llm 三层兜底）、cs_supervisor（规则优先+LLM 兜底）
- **规则/执行节点**: 主图 supervisor 是纯规则 DAG 调度器（不调 LLM）；客服 5 专家中仅 knowledge 走 LLM（经 RAG），query/action/complaint/handoff 为业务服务编排
- **工具节点（13）**: skills/registry.py 注册的 Skill，非决策 agent；LangChain tool 见 backend/tools/（含 memory_search/memory_store/calculate）

### Capability DAG

```json
{
  "nodes": {
    "1": {"step_id": "1", "capability": "sql.query", "params": {"question": "..."}},
    "2": {"step_id": "2", "capability": "business.analyze", "params": {}}
  },
  "edges": {"2": ["1"]}
}
```

### 已注册 Capability（13 个）

| capability | Skill | 节点名 |
|---|---|---|
| sql.query | SQLSkill | sql_skill |
| business.analyze | BusinessAnalysisSkill | business_analysis_skill |
| rag.search | RAGSkill | rag_skill |
| report.generate | ReportSkill | report_skill |
| email.send | EmailSkill | email_skill |
| data.export | DataExportSkill | data_export_skill |
| web.search | WebSearchSkill | web_search_skill |
| web.crawl | WebCrawlSkill | web_crawl_skill |
| data.collect | DataCollectionSkill | data_collection_skill |
| competitor.analyze / .watch / .history | CompetitorAnalysisSkill | competitor_analysis_skill |
| travel.poi_search | TravelPoiSkill | travel_poi_skill |

新增 Skill: 创建 `skills/<name>/skill.py` → `skills/registry.py` 注册 → 自动发现。

### SQL 子系统

```
SQLSkill → SQLAgent → Router → Generator → Validator(6层) → RowSecurity → Executor(连接池) → PostgreSQL
```

6 层安全: ①SELECT 类型校验 ②表名白名单 ③敏感列拒绝 ④禁止函数黑名单 ⑤LIMIT 强制 ⑥agent_readonly 只读角色

数据协议:
- **SQLResult** (Pydantic): sql/tables/columns/rows/row_count/execution_time — Skill 层输出
- **BusinessInsight** (Pydantic): summary/risks/suggestions/confidence — BusinessAnalyzer 输出

步骤间数据传递: Supervisor 在 Send 中注入 `previous_outputs`（前置步骤的 output 自动传给后置步骤）。

### 数据库

7 schema × 18 表: product / order / inventory / customer / crawler / finance / ai
连接池: ThreadedConnectionPool（min=2, max=10）
只读账号: agent_readonly（scram-sha-256 认证）
Migration: `sql/migrations/001~005`

### 旅游规划域图（`backend/travel/`，P0）

与 `customer_service/` 同级的独立域图，接入方式完全一致：`travel/register.py`
自注册 → `backend/domains/__init__.py` 触发 → builder 自动布线，**不改 builder.py**。

数据契约（对齐 SQLResult / BusinessInsight 口径，Pydantic）：
  `TravelBrief`（需求）→ `Poi`（候选）→ `Itinerary`（输出，含 warnings/sources/confidence）
  状态里只存 dict（`load_*/save_*` 转换），保证开启 checkpointer 时可序列化。

**validator 是旅游域的 Evidence Gate**：RAG 的 Gate 解决「没有依据就别答」，
这里解决「这份行程物理上成不成立」。四条硬纪律：
  ① 纯规则、零 LLM、零 IO —— 正确性不押在模型上，也不能有网络抖动
  ② 只判定不修改 —— 修复动作在 `repair.py`，两者独立演进、独立单测
  ③ error 阻塞交付并触发修复；warning 只提示（夜里逛夜市是合理需求）
  ④ 四轴：时间（营业时段/闭馆日/重叠/长等候）· 地理（长通勤/在途总量/重复到访）
     · 体力（单日 POI 数与**纯到访**时长，不含通勤与用餐）· 预算（超支/逼近上限）

**局部修复**（`repair.py`）：只处理被点名的天与条目，不整条重规划；
用户点名必去的条目**永不被静默丢弃**，命中违规时保留并记为 kept_required 告知用户。

**为什么 travel.plan 不注册成主图 Skill**：行程生成需要「槽位追问 → 骨架 → 排程 →
校验 → 修复」的有状态多步流程，已由域图承担；再包一层 Skill 会产生第二套实现
且少了约束校验这道安全网。POI 候选检索是无状态单点能力，故注册为 `travel.poi_search`。

数据源：P0 用本地种子数据（`tools/travel/poi_seed.py`，source=`seed:local`，
坐标/营业时间/票价为**示例值**，非权威），reporter 会如实标注来源与置信度。
P1 替换为地图/票务 MCP 供给，`Poi`/`Itinerary` 契约不变。

开关：`TRAVEL_ENABLED`（默认 false，与 CS_ENABLED 同策略），
阈值集中在 `config/travel.py`，校验器只读该文件，不散落魔数。

**跨轮契约（checkpointer 开启时才生效，但契约必须一直遵守）**

1. `new_travel_graph_input()` **只放本轮输入**，不得预置产物/执行态默认值。
   checkpointer 把 input 当作对上一轮状态的**更新**合并，预置
   `brief: {} / itinerary: None / expert_history: []` 等于每轮清空成果
   （实测表现：第二轮槽位全丢、跨轮改单完全失效）。
2. 相应纪律：读状态一律 `.get()` —— 「本轮没写过的键」不会出现在最终状态里。
3. 需求变化靠 `brief_fingerprint` 判定：指纹变 → `planning_reset()` 清空规划产物重排。
   只在 slot_filler 里做（它是每轮唯一改写 brief 的地方）。若不清，
   supervisor 会看到「专家都跑过 + validation 通过」直接进 reporter，
   把**上一轮行程**当成新需求的结果输出 —— 比不持久化更糟。

**checkpointer 现状（2026-09-14 已打通）**：三处 `_build_checkpointer`（主图 / 客服域 /
旅游域）都按「postgres 优先、失败降级 MemorySaver」实现。PostgresSaver 需要
psycopg **v3** 与 `langgraph-checkpoint-postgres` —— 依赖在 `pyproject.toml` 与
`requirements-lock.txt` 中**都已声明**，Docker 镜像照 pyproject 装（生产侧不缺），
缺的只是本地 venv；已在 `.venv` 补齐，并修掉锁文件下面那两个坑。
`config/startup.py` 有启动校验：开关开着但**当前环境**导入不到驱动 → warning 级点名
（说清"会静默降级为 MemorySaver"的后果）。它只探测 import、不探测数据库连通性 ——
启动期不该因为 PG 还在启动就报错。

已修的两处依赖问题（**照旧锁文件装会失败**，务必留意）：
1. `requirements-lock.txt` 原把 `langgraph-checkpoint` 钉在 **4.0.3**，而
   `langgraph-checkpoint-postgres==3.1.0` 要求 **>=4.1.0** —— 一组自相矛盾、
   无法求解的依赖集。已升到 **4.2.0**。
2. Windows / 无 libpq 环境下只装 `psycopg` 会报
   `ImportError: no pq wrapper available`，必须装 `psycopg[binary]`。

checkpoint TTL 清理已收敛到 `orchestration/graph/checkpointer_cleanup.py`
（三方共用同一组表，**全进程单例**：谁先启动谁的 TTL 生效，
`customer_service/checkpointer_cleanup.py` 保留为兼容薄壳）。改 TTL 必须三处一起改。

## Design Principle

必须满足: 可理解、可测试、可观测、可维护、可扩展、可控制、可靠性
禁止: Demo 跑通式开发、临时堆叠、except Exception: pass

### Priority

P0: 数据错误、安全问题、崩溃、Trace 丢失
P1: 架构问题、强耦合、重复代码
P2: 命名、注释

### 数据库生产标准

P0: 只读角色 + scram-sha-256 + 连接池 + connect_timeout/keepalives
P1: 外键完整 + CHECK 约束 + 高频列索引

## Code Rules

- Python: snake_case、类型注解、logger 替代 print、具体异常、SQL 参数化
- 禁止: 业务代码直接 os.getenv、文件名 misc/helper/common/utils2
- Tool 必须独立可测试

### 写操作审批门（human-in-the-loop）

写副作用工具（send_email/export_csv/data_collection/competitor 写动作）执行前
必须调用 `security/tool_approval.ensure_approved()`：TOOL_APPROVAL_MODE=required
（默认）时建审批单返回待批提示，管理员经 `/api/approvals` 批准后重试同指纹操作
放行（TTL 内）。表: ai.tool_approval_requests（migration 007）。工具层身份来自
`tools/session.get_tool_user_id()`（RequestContext.bind 注入），禁止硬编码 user_id。

### 工具契约兼容规则（代替 per-schema 版本号）

工具/Skill 契约（params_schema、output_type、capability 名）与消费方（Planner、
Reporter）同仓同发布，schema 变更与消费方适配原子提交，**不加 per-schema 版本号**。
兼容靠三条规则：

1. **向后兼容演进**：只新增可选参数；不改既有参数语义；不删除/改名已有字段
   （proto 演进规则）。破坏性变更 = 新 capability 名 + 旧Capability 保留一个废弃期
2. **测试守护**：`test_registry_consistency.py`（注册表一致性）+
   `test_base_output_contract.py`（输出契约）+ e2e 离线故障注入集
   （`datasets/e2e/cases.jsonl` F-* 用例）构成契约回归门
3. **变更跑评估**：改 params_schema/描述/prompt 后跑 planner 评估（live），
   关键写操作参数用 `datasets/planner_params.json` 的 expected.params 断言

只有当工具以独立部署制品对外（MCP Server 发布、跨团队共享）时，才引入显式版本号。

### 主图 LangGraph 保护

- recursion_limit: MAIN_GRAPH_RECURSION_LIMIT（默认 80），runner 每次 stream 传入
- checkpointer: MAIN_GRAPH_CHECKPOINTER_ENABLED（默认关）；开启后 request_context
  以 checkpoint_safe() dict 进状态（trace/sink 不序列化），thread_id 每轮唯一

## Change Flow

明确目标 → 阅读代码 → 分析影响 → 修改 → 测试
Bug 先复现、Refactor 测试通过、Feature 优先补测试

## Validation

Backend: `py_compile` + `pytest tests/sql/ -v`
Frontend: `npx tsc --noEmit` + `npm test`
E2E: `cd backend && python e2e_demo.py`

## Docs

设计文档: `docs/README.md`（7 个顶层文档 + 7 个关键深读）
记忆: 用户级 `~/.claude/projects/<project>/memory/MEMORY.md`（按项目分类的会话记忆）

## 服务启停约定（2026-09-15 起生效）

```bash
# Python 后端 :8000（uvicorn --reload --reload-dir app，改代码即生效）
start_py.bat / stop_py.bat / restart_py.bat
# Java 服务（原生 jar：auth-service :8006 / system-service :8001 / api-gateway :8080；
# business-service 留容器；mysql/redis/nacos/postgres/kafka 基础设施容器不动）
start_java.bat / stop_java.bat / restart_java.bat
# 改了 Java 源码 → 本地 mvn 打包（免 docker build）→ restart_java.bat
build_java.bat
# 后端+前端一起（冷启动场景）；start.bat 按 .env.local 自动选模式
start_all.bat / stop_all.bat / restart_all.bat
```

- 前端 :3000 用 `start_all.bat` / `frontend/start_dev.bat`；`NEXT_PUBLIC_*` 改动需重启 dev server
- 网关路由/白名单改 `api-gateway/config/application.yml`（compose 挂载的外部配置，
  唯一事实源）→ `restart_java.bat` 生效，**无需 docker build**
- 热路由（仅本机开发）：`set GATEWAY_HOT_ROUTES=true` 后 start_java.bat，
  可 `POST /actuator/gateway/routes/{id}` 运行时增改路由；生产必须保持关闭
- 登录链路：前端 `/login` → 网关 `/api/auth/**` → auth-service(JWT)；
  refresh_token 走 HttpOnly Cookie（REFRESH_COOKIE_SECURE 本地必须 false）
- 杀端口脚本都带 docker 守卫：容器占端口时跳过，防误杀 com.docker.backend
- 完整文档: `命令文档.md`（含热路由 curl 示例、两种模式说明）
