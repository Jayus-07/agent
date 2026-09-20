# 客服系统改造设计报告

> 基于现有 Agent Platform 架构扩展企业级智能客服系统
>
> 日期：2026-09-03 | 状态：设计评审阶段

---

## 目录

1. [当前架构分析](#1-当前架构分析)
2. [改造边界：复用 / 修改 / 新增](#2-改造边界复用--修改--新增)
3. [客服 Router 设计](#3-客服-router-设计)
4. [请求分类：三种类型](#4-请求分类三种类型)
5. [客服 Skill 设计](#5-客服-skill-设计)
6. [身份与权限模型](#6-身份与权限模型)
7. [高风险操作确认机制](#7-高风险操作确认机制)
8. [客服 RAG 设计](#8-客服-rag-设计)
9. [客服 Memory 设计](#9-客服-memory-设计)
10. [人工接管设计](#10-人工接管设计)
11. [投诉处理设计](#11-投诉处理设计)
12. [数据库设计](#12-数据库设计)
13. [客服 LangGraph 流程](#13-客服-langgraph-流程)
14. [错误处理设计](#14-错误处理设计)
15. [安全治理设计](#15-安全治理设计)
16. [测试方案](#16-测试方案)
17. [评估指标](#17-评估指标)
18. [架构冲突与风险分析](#18-架构冲突与风险分析)
19. [实施路线图](#19-实施路线图)

---

## 1. 当前架构分析

### 1.1 整体请求流程

```
用户请求 (HTTP)
    │
    ▼
┌─────────────────────────────────────────────────┐
│ FastAPI Middleware Chain                         │
│  1. API Key Auth (api/middleware/auth.py)        │
│  2. Upload Size Limit (inline)                   │
│  3. Concurrency Limiter (semaphore)              │
│  4. CORS                                         │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│ Route Handler (app/api/routes/chat.py)           │
│  POST /chat  →  agent.ask()                      │
│  POST /chat/stream  →  agent.stream_events()     │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│ MultiAgentSystem (orchestration/graph/system.py) │
│                                                  │
│  ① Input Guard (security/input_guard/)           │
│     → BLOCK/CLARIFY 直接拦截，不进入 Graph        │
│                                                  │
│  ② Trace Start + Session/Memory Load             │
│     memory_manager.start_session(session_id)     │
│                                                  │
│  ③ make_initial_state → graph.invoke/stream      │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│ LangGraph StateGraph                             │
│                                                  │
│  START → router ─┬─(direct)──→ skill_executor    │
│                  ├─(workflow)→ workflow_executor  │
│                  └─(plan)────→ planner            │
│                                → critique         │
│                                → supervisor ⇄     │
│                                  Send[]{skills}   │
│                                → reporter         │
│                                → END              │
└─────────────────────────────────────────────────┘
```

### 1.2 核心模块详细分析

#### Router（三层降级路由）

| 层级 | 模块 | 延迟 | 阈值 |
|------|------|------|------|
| L1 RuleRouter | `orchestration/router/rule_router.py` | ~1ms | regex 关键词，conf ≥ 0.8 直接决定 |
| L2 VectorRouter | `orchestration/router/vector_router.py` | ~30ms | Chroma 嵌入相似度，conf ≥ 0.85 决定，0.6-0.85 可接受 |
| L3 LLMRouter | `orchestration/router/llm_router.py` | ~3-5s | qwen2.5:3b 兜底，始终返回结果 |

**输出**：`RouteDecision(execution_mode, candidates[], confidence, reason, workflow_name)`

- `execution_mode`: `DIRECT` / `PLAN` / `WORKFLOW`
- Router 决定 HOW（执行模式）和 WHO（候选能力），但不绑定；Planner 做最终 DAG

**当前能力列表**（12 capabilities）：
- `sql.query`, `rag.search`, `report.generate`, `email.send`, `data.export`
- `web.search`, `web.crawl`, `data.collect`, `business.analyze`
- `competitor.analyze`, `competitor.watch`, `competitor.history`

#### Planner / Critique / Supervisor

- **Planner** (`agents/planner/planner.py`): LLM 生成 DAG 执行计划，带 5min TTL LRU 缓存
- **Critique** (`agents/planner/critique.py`): 规则引擎 → 自动修复 → 可选 LLM 纠正
- **Supervisor** (`orchestration/supervisor/scheduler.py`): 循环调度，最大 10 轮，并行 Send 分发，降级链 `sql.query ↔ rag.search`

#### Skill 系统

```
BaseSkill (skills/base.py)
├── capabilities: list[str]       # 能力标识
├── description: str              # Planner 提示词用
├── params_schema: dict           # 参数描述
├── examples: list[str]           # 示例
├── _tool_fn → LangChain Tool    # 底层工具函数
└── execute(state, step) → dict   # 执行入口（可覆盖）
```

**注册流程**：
1. `skills/<name>/__init__.py` 定义 node fn 并调用 `tool_registry.register_skill_node()`
2. `skills/registry.py` import 触发注册
3. `ToolRegistry` 自动从 `backend.skills.registry._registry` 派生 CAPABILITY_MAP / CAPABILITY_SCHEMA

#### RAG 系统

完整管线：
```
文档上传 → Parser → DocumentAST → Cleaner → StructureAnalyzer
→ ChunkStrategyRouter → 8种分块策略 → leaf+parent chunks
→ Metadata (keywords, simulated_questions, MinHash, summary, entities)
→ Embedding (BGE-small-zh, Document Expansion)
→ ChromaDB (chunk-level) + ChromaDB (doc-level) + BM25Store

查询:
QueryAnalyzer (纯规则, ~5ms) → KBRouter → build_kb_filter
→ ChunkLevelRetriever (Stage-1 doc-level → Stage-2 hybrid RRF)
→ MultiQuery (LLM 改写) → Rerank (DashScope/Local)
→ QA Chain (stuff_documents + [En]引用 + META注释)
→ Evidence Gate (3层: Retrieval/Rerank/Generation)
→ ClaimVerifier (确定性事实验证)
→ Faithfulness (LLM-as-Judge)
→ CitationFormatter → Answer + References
```

**KB 管理**：
- 7 个现有 KB: `biz_inventory`, `biz_order`, `biz_product`, `policy_hr`, `policy_finance`, `policy_general`, `rag_test_kb`
- `kb_id` 由文档目录一级子目录派生
- `KBRouter` 关键词路由 → `build_kb_filter` → Chroma `$or` 过滤

#### SQL Agent

```
NL Question → Router (关键词+LLM选表)
→ Generator (LLM生成SQL)
→ Validator (6层: 语句类型/表白名单/敏感列/禁函数/LIMIT/执行层)
→ RowSecurity (AST改写注入行过滤)
→ Executor (只读角色 + readonly transaction + statement_timeout)
```

**安全约束**：
- 仅允许 SELECT（AST 层 + 事务层 + DB 角色三重防御）
- 只读连接角色 `agent_readonly`
- 敏感列拒绝、禁函数黑名单
- 自动 LIMIT 100、5s 超时
- 列掩码（如 `customer.customers.name → "张***"`）
- 行级安全（`SQL_ROW_SECURITY_ENABLED`，默认关闭）

**关键判断**：SQL Agent 不能直接暴露给客服用户。客服查询必须通过业务 Skill/Service 层。

#### Memory 系统

| 层级 | 模块 | 存储 | 容量 |
|------|------|------|------|
| L1 Short-term | `memory/short_term.py` | 内存 ring buffer | 20 条 (10 轮) |
| L2 Session | `memory/session.py` | PostgreSQL `chat_sessions` | 50 条触发摘要 |
| L3 Long-term | `memory/long_term.py` | PostgreSQL + pgvector | 余弦去重 0.85, 覆盖 0.92 |

**当前状态**：`user_id` 在 schema 和查询层已支持，但调用链实际使用 `"default"` — 多用户隔离尚未激活。

#### Observability

- **Tracer** (`observability/tracer.py`): TraceRecord + Span 模型，Langfuse 主存储 + SQLite 兜底
- **Metrics** (`observability/metrics.py`): Prometheus，4 核心 SLI + 运营 gauge
- **Alerts** (`observability/alerts.py`): PlanAlert + JSONL 日志 + Webhook 推送
- **TraceMiddleware**: LangGraph 节点自动 span 包装

#### 认证/授权

- API Key 认证 (`X-API-Key` header)，fail-closed
- 用户身份仅通过可信网关头 `X-User-Id`（`TRUST_USER_HEADER=true` 时）
- 无 JWT/OAuth/用户账户系统

### 1.3 现有数据库 Schema

**Business DB** (`agent_business`): 7 schemas × ~19 tables

```
product:    products, categories, product_tags
"order":    orders, order_items, refunds
inventory:  inventory, warehouses, purchase_orders
customer:   customers, customer_behavior
crawler:    competitor_products, competitor_price, product_reviews
finance:    expenses, daily_profit
ai:         agent_tasks, agent_trace
```

**Memory DB** (`agent_memory`):
```
chat_sessions:   session_id, user_id, summary, context_summary
chat_messages:   FK→chat_sessions, role, content
memory_records:  UUID PK, user_id, embedding vector(512), importance_score
```

**关键发现**：`customer.customers` 和 `"order".orders` 已存在，`orders` 有 `customer_id` 字段。

---

## 2. 改造边界：复用 / 修改 / 新增

### 2.1 直接复用

| 模块 | 路径 | 复用方式 |
|------|------|----------|
| RAG 检索管线 | `backend/rag/` | 完整复用：Hybrid Retrieval, Reranker, MultiQuery, Evidence Gate, Citation, Faithfulness |
| RAG 索引管线 | `backend/rag/indexing/` | 复用 IncrementalIndexer, DocumentRegistry, ChunkStore |
| RAG 预处理 | `backend/rag/preprocessing/` | 复用 Parser, AST, Chunking, Metadata |
| Embedding | `backend/rag/embedding_singleton.py` | 复用 BGE 嵌入单例 |
| LLM 基础设施 | `backend/infra/llm/` | 复用 LLMFactory, proxy, rate_limiter, circuit_breaker |
| Memory L1/L2/L3 | `backend/memory/` | 复用三层记忆架构（需扩展 user_id 传播） |
| Tracer | `backend/observability/tracer.py` | 复用全链路追踪 |
| Metrics | `backend/observability/metrics.py` | 复用 Prometheus 指标框架 |
| Alerts | `backend/observability/alerts.py` | 复用告警系统 |
| Input Guard | `backend/security/input_guard/` | 复用输入安全检查 |
| Skill 注册机制 | `backend/skills/registry.py` | 复用 Skill 自注册模式 |
| Tool Registry | `backend/orchestration/tool_registry.py` | 复用能力映射 |
| BaseSkill | `backend/skills/base.py` | 复用技能基类 |
| LangGraph 框架 | `backend/orchestration/graph/` | 复用 StateGraph 构建、Supervisor 调度 |
| TraceMiddleware | `backend/observability/trace_middleware.py` | 复用节点自动追踪 |

### 2.2 需要修改

| 模块 | 文件 | 修改内容 | 影响评估 |
|------|------|----------|----------|
| Router types | `orchestration/router/types.py` | 新增客服域 capabilities | 向后兼容，仅扩展枚举 |
| Rule Router | `orchestration/router/rule_router.py` | 新增客服意图关键词规则 | 不影响现有规则优先级 |
| Vector Router | `orchestration/router/vector_router.py` | 新增客服意图嵌入示例 | 仅增加种子数据 |
| Agent State | `orchestration/state.py` | 新增客服上下文字段 | TypedDict 扩展，向后兼容 |
| KB 注册表 | `config/knowledge_base.py` | 新增客服 KB 定义 | 仅扩展注册表 |
| KB 路由规则 | `config/kb_rules.py` | 新增客服 KB 路由规则 | 仅扩展规则 |
| Memory user_id | `memory/manager.py`, `memory/service.py` | 激活 user_id 传播链路 | 需确保 "default" 兼容 |
| Reporter | `agents/reporter/reporter.py` | 客服场景回答风格适配 | 通过 context 控制，不影响默认行为 |
| Graph builder | `orchestration/graph/builder.py` | 注册客服 skill 节点 | 自动发现，无需硬编码 |
| API router | `app/api/router.py` | 挂载客服路由 | 仅增加 include |
| Config | `config/__init__.py` | 新增客服配置项 | 仅扩展 |
| SQL schema_config | `sql/data/schema_config.py` | 注册客服相关表到允许列表 | 仅扩展白名单 |
| SQL row_security | `sql/row_security.py` | 增加客服场景行安全规则 | 增强安全性 |

### 2.3 全新新增

```
backend/customer_service/              # 客服业务域
├── __init__.py
├── router/                            # 客服专用分层 Router
│   ├── __init__.py
│   ├── types.py                       # 客服意图类型定义
│   ├── coarse_router.py               # 第一层：粗分类
│   └── fine_router.py                 # 第二层：细分类
├── skills/                            # 客服业务 Skills
│   ├── order/                         # 订单查询
│   ├── logistics/                     # 物流查询
│   ├── after_sales/                   # 售后服务
│   ├── refund/                        # 退款退货
│   ├── complaint/                     # 投诉处理
│   └── human_handoff/                 # 人工转接
├── workflow/                          # 客服业务工作流
│   ├── confirmation.py                # 确认状态机
│   ├── refund_workflow.py             # 退款工作流
│   ├── return_workflow.py             # 退货工作流
│   └── complaint_workflow.py          # 投诉工作流
├── security/                          # 客服安全层
│   ├── permission.py                  # 权限校验
│   ├── input_guard.py                 # 客服输入守卫
│   └── output_guard.py               # 输出守卫
├── audit/                             # 审计
│   ├── audit_log.py                   # 审计日志
│   └── action_log.py                  # 操作记录
├── handoff/                           # 人工转接
│   ├── handoff_manager.py             # 转接管理
│   └── ticket.py                      # 工单管理
├── models/                            # 数据模型
│   ├── conversation.py                # 会话模型
│   ├── action.py                      # 操作模型
│   └── enums.py                       # 枚举定义
└── service/                           # 业务服务层
    ├── order_service.py               # 订单服务
    ├── logistics_service.py           # 物流服务
    ├── after_sales_service.py         # 售后服务
    ├── refund_service.py              # 退款服务
    └── user_service.py                # 用户服务

backend/skills/customer_*/             # 客服 Skill 包（注册到现有 Skill 系统）
├── order_query/
├── logistics_query/
├── after_sales/
├── refund/
├── complaint/
└── human_handoff/

backend/sql/migrations/006_*.sql       # 客服数据库迁移

backend/app/api/routes/
├── customer_service.py                # 客服 API 路由
└── customer_auth.py                   # 客服认证路由

backend/tests/customer_service/        # 客服测试
├── test_coarse_router.py
├── test_fine_router.py
├── test_order_skill.py
├── test_logistics_skill.py
├── test_after_sales_skill.py
├── test_refund_skill.py
├── test_complaint_skill.py
├── test_human_handoff.py
├── test_permission.py
├── test_confirmation.py
├── test_audit.py
├── test_input_guard.py
├── test_output_guard.py
├── test_rag_customer.py
└── test_integration.py
```

---

## 3. 客服 Router 设计

### 3.1 设计原则

不把客服意图塞进现有 Router 的 12 个 capability 中。采用**分层 Router**，作为现有 Router 的前置层。

```
用户输入
    │
    ▼
┌──────────────────────┐
│ Input Guard (复用)    │  ← 安全检查，BLOCK/CLARIFY 拦截
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ Domain Detector      │  ← 判断是否为客服域请求
│ (新增)               │
└──────────┬───────────┘
           │
     ┌─────┴─────┐
     │           │
  客服域      非客服域
     │           │
     ▼           ▼
┌──────────┐  ┌──────────────┐
│ CS Router│  │ Existing     │
│ (新增)   │  │ Router (复用) │
└──────────┘  └──────────────┘
```

### 3.2 Domain Detector

判断请求是否属于客服域。使用规则 + 向量相似度双通道：

```python
# 规则通道（~1ms）
CS_DOMAIN_PATTERNS = [
    # 订单相关
    r"(我的|查|看).{0,4}(订单|购买|下了.*单)",
    r"(发货|到货|物流|快递|包裹|运输)",
    r"(退款|退货|换货|退换|售后)",
    r"(投诉|举报|差评)",
    r"(人工|客服|转接|真人)",
    # 商品咨询
    r"(这个商品|这款|能不能|可以退|保修|尺寸|尺码)",
    r"(优惠|活动|促销|折扣|券)",
    # 账户
    r"(我的账户|修改.*地址|修改.*信息|密码)",
]

# 向量通道（~30ms）
# Chroma collection: cs_domain_v1
# 种子示例: 每个粗分类 10-15 个示例
```

**判定逻辑**：
- 规则通道命中 2+ 模式 → `is_cs_domain = True, confidence ≥ 0.85`
- 规则通道命中 1 模式 → hint，交给向量通道
- 向量通道 top1 相似度 ≥ 0.7 → `is_cs_domain = True`
- 均不命中 → `is_cs_domain = False`，走现有 Router

> ⚠️ **实现已变更（2026-09-18 起）**：本节 §3.2 描述的**向量通道已删除**
> （提交 `3f88b4f`，2026-09-18），现行实现见 `backend/customer_service/router/domain_detector.py`
> ——**纯正则**单通道（`CS_DOMAIN_PATTERNS` 命中 ≥ `CS_RULE_MIN_HITS` 即判客服域；
> `vector_score` 字段保留但恒 `0.0`，仅为消费方兼容）。上表「规则命中 1 模式 → 交给向量通道」
> 与「向量 top1 相似度 ≥ 0.7」两条**均已不存在**。由此产生的语义类问法召回缺口
> 由**客服窗口锁域**（前端客服抽屉带 `domain_hint=customer_service` → 跳过判域）兜底。
> 删除通道的动机是 2026-09-17 全站存储收口 pgvector，见 `docs/chroma-pgvector迁移方案-2026-09-17.md`。

### 3.3 第一层：粗分类 Router

```python
class CSDomain(str, Enum):
    KNOWLEDGE = "knowledge"        # 知识问答
    TRANSACTION = "transaction"    # 业务查询
    ACCOUNT = "account"           # 账户操作
    AFTER_SALES = "after_sales"   # 售后
    COMPLAINT = "complaint"       # 投诉
    HUMAN = "human"               # 人工转接
    UNKNOWN = "unknown"           # 未识别

@dataclass
class CSRouteResult:
    domain: CSDomain
    confidence: float
    requires_auth: bool            # 是否需要身份验证
    requires_action: bool          # 是否涉及状态变更
    risk_level: str                # low / medium / high / critical
    reason: str
```

**分类规则**（RuleRouter，~1ms）：

| Domain | 关键词模式 | 置信度 |
|--------|-----------|--------|
| HUMAN | `人工客服`, `转人工`, `找真人`, `找客服` | 0.95 |
| COMPLAINT | `投诉`, `举报`, `太差`, `不满意`, `差评` | 0.90 |
| AFTER_SALES | `退款`, `退货`, `换货`, `售后`, `退换` | 0.85 |
| TRANSACTION | `订单`, `物流`, `快递`, `发货`, `到货`, `支付` | 0.80 |
| ACCOUNT | `修改地址`, `修改信息`, `账户`, `密码` | 0.80 |
| KNOWLEDGE | `几天`, `政策`, `规则`, `怎么`, `如何`, `什么` | 0.70 |

**向量 Router**（VectorRouter，~30ms）：
- Chroma collection: `cs_coarse_v1`
- 每个 domain 15-20 个种子示例
- 相似度 `score = 1/(1+distance)`，top1 score 作为 confidence

**LLM Router**（兜底，~2-3s）：
- 精简 prompt (<300 tokens)，输出结构化 JSON
- 仅在规则 + 向量均低于阈值时触发

### 3.4 第二层：细分类 Router

根据粗分类结果，选择对应的细分类器：

```python
FINE_INTENTS = {
    CSDomain.KNOWLEDGE: {
        "product_faq":       "商品常见问题（尺寸、材质、功能等）",
        "policy":            "平台政策（注册、支付、隐私等）",
        "promotion":         "促销活动（优惠券、满减、折扣等）",
        "logistics_policy":  "物流政策（配送范围、时效等）",
        "after_sales_policy":"售后政策（退货期限、退款规则等）",
    },
    CSDomain.TRANSACTION: {
        "query_order":         "查询订单列表",
        "query_order_status":  "查询订单状态",
        "query_order_items":   "查询订单商品明细",
        "query_payment":       "查询支付状态",
        "query_logistics":     "查询物流轨迹",
    },
    CSDomain.ACCOUNT: {
        "update_address":    "修改收货地址",
        "update_info":       "修改账户信息",
        "reset_password":    "重置密码",
        "query_account":     "查询账户信息",
    },
    CSDomain.AFTER_SALES: {
        "check_return_eligibility": "检查退货资格",
        "create_return":            "申请退货",
        "check_refund_eligibility": "检查退款资格",
        "create_refund":            "申请退款",
        "query_after_sales":        "查询售后进度",
    },
    CSDomain.COMPLAINT: {
        "complaint":       "提交投诉",
        "escalation":      "升级处理",
    },
    CSDomain.HUMAN: {
        "transfer_to_human": "转接人工客服",
    },
}
```

**细分类实现**：
- KNOWLEDGE / HUMAN / COMPLAINT：规则即可（意图数少，区分度高）
- TRANSACTION / AFTER_SALES / ACCOUNT：向量相似度 + 可选 LLM 兜底

### 3.5 Router 输出格式

```json
{
    "domain": "after_sales",
    "intent": "check_refund_eligibility",
    "confidence": 0.94,
    "requires_auth": true,
    "requires_action": false,
    "risk_level": "low",
    "route_path": "business_query",
    "kb_id": "after_sales",
    "metadata": {
        "order_id_mentioned": true,
        "product_id_mentioned": false
    }
}
```

**`route_path` 映射**：

| route_path | 处理路径 | 适用意图 |
|------------|---------|---------|
| `knowledge_query` | RAG → Evidence Gate → Answer | KNOWLEDGE.* |
| `business_query` | Skill → 权限检查 → DB/API → Answer | TRANSACTION.*, ACCOUNT.query_* |
| `business_action` | Workflow → 确认 → 执行 → 审计 | AFTER_SALES.create_*, ACCOUNT.update_* |
| `complaint_flow` | 创建工单 → 保存上下文 → 转人工 | COMPLAINT.* |
| `human_handoff` | 直接转人工 | HUMAN.* |

### 3.6 与现有 Router 的集成

```python
# orchestration/graph/router_node.py 修改
def router_node(state):
    query = state["question"]

    # 新增：客服域检测
    cs_result = detect_cs_domain(query)
    if cs_result.is_cs_domain:
        # 客服域请求：走客服分层 Router
        cs_route = cs_router.route(query)
        state["route_decision"] = cs_route.to_decision_dict()
        state["route_mode"] = "customer_service"
        state["cs_route"] = cs_route
        return state

    # 非客服域：走现有 Router
    router = get_router()
    decision = router.route(query)
    state["route_decision"] = asdict(decision)
    state["route_mode"] = decision.execution_mode.value
    return state
```

**向后兼容**：`route_mode = "customer_service"` 是新增模式，现有 `direct/plan/workflow` 不受影响。Graph builder 增加 `customer_service` 条件边。

---

## 4. 请求分类：三种类型

### 4.1 Knowledge Query（知识问答）

**特征**：用户询问静态知识、政策、规则、FAQ

**示例**：
- "你们支持几天无理由退货？"
- "一般几天发货？"
- "优惠券怎么使用？"

**处理路径**：
```
CS Router (domain=KNOWLEDGE)
    │
    ▼
kb_id 选择 (根据 intent 映射)
    │
    ▼
RAG Pipeline (复用现有)
    │  QueryAnalyzer → Hybrid Retrieval → Rerank
    │  → Evidence Gate → Citation → Faithfulness
    ▼
Answer + References
```

**关键约束**：
- 必须走 Evidence Gate，不允许 LLM 自由发挥
- 置信度 < 0.60 必须拒答
- 回答必须附带引用来源

### 4.2 Business Query（业务查询）

**特征**：用户查询实时业务数据（订单、物流、账户等）

**示例**：
- "我的订单到哪里了？"
- "帮我查一下最近的订单"
- "我的退款进度怎样了？"

**处理路径**：
```
CS Router (domain=TRANSACTION, intent=query_*)
    │
    ▼
Permission Check (user_id 验证)
    │
    ▼
Business Skill (order/logistics/after_sales)
    │  参数校验 → 权限校验 → DB 查询（通过 Service 层）
    ▼
Business Result (来自 DB/API，非 LLM)
    │
    ▼
Response Formatting (结构化数据 → 自然语言)
```

**关键约束**：
- 业务数据**必须**来自 DB/API，禁止 LLM 编造
- 查询必须绑定 `user_id`，禁止仅凭 `order_id` 查询
- 返回数据需经过 Output Guard 过滤（防止泄露其他用户信息）

### 4.3 Business Action（业务操作）

**特征**：用户请求执行会产生状态变更的操作

**示例**：
- "帮我退款"
- "我要退货"
- "修改我的收货地址"
- "取消订单"

**处理路径**：
```
CS Router (domain=AFTER_SALES, intent=create_*)
    │
    ▼
Permission Check (user_id + 操作权限)
    │
    ▼
Get Business Context (获取订单/商品等上下文)
    │
    ▼
Business Rule Check (退货期限、退款条件等)
    │
    ▼
Risk Assessment (风险评估)
    │
    ▼
Generate Action Proposal (生成操作提案)
    │
    ▼
User Confirmation (等待用户明确确认)
    │  ← 状态机: PENDING_CONFIRMATION → USER_CONFIRMED
    ▼
Execute Action (通过 Service 层执行)
    │
    ▼
Audit Log (记录操作审计)
    │
    ▼
Return Result
```

**关键约束**：
- 禁止 LLM 直接生成 SQL 执行写操作
- 所有操作必须经过确认状态机
- "用户说帮我退款" ≠ "用户确认退款"
- 每个操作必须记录审计日志
- 高风险操作需要二次确认

---

## 5. 客服 Skill 设计

### 5.1 Skill 总览

```
客服 Skills（注册到现有 Skill Registry）
│
├── order_query_skill        # 订单查询
│   capabilities: ["cs.order_query"]
│   路由: TRANSACTION.query_order / query_order_status / query_order_items / query_payment
│
├── logistics_query_skill    # 物流查询
│   capabilities: ["cs.logistics_query"]
│   路由: TRANSACTION.query_logistics
│
├── after_sales_skill        # 售后服务
│   capabilities: ["cs.after_sales_check", "cs.after_sales_action"]
│   路由: AFTER_SALES.*
│
├── refund_skill             # 退款退货
│   capabilities: ["cs.refund_check", "cs.refund_action"]
│   路由: AFTER_SALES.create_refund / create_return
│
├── complaint_skill          # 投诉处理
│   capabilities: ["cs.complaint"]
│   路由: COMPLAINT.*
│
├── human_handoff_skill      # 人工转接
│   capabilities: ["cs.human_handoff"]
│   路由: HUMAN.*
│
└── account_skill            # 账户操作
    capabilities: ["cs.account_query", "cs.account_action"]
    路由: ACCOUNT.*
```

### 5.2 Skill 实现模式

每个客服 Skill 继承 `BaseSkill`，但**覆盖 `execute()` 方法**以实现严格的业务逻辑控制：

```python
class OrderQuerySkill(BaseSkill):
    name = "cs_order_query"
    capabilities = ["cs.order_query"]
    description = "查询用户订单信息，包括订单列表、订单状态、商品明细、支付状态"
    params_schema = {
        "user_id": {"type": "string", "required": True, "description": "用户ID（系统注入）"},
        "order_id": {"type": "string", "required": False, "description": "订单ID"},
        "query_type": {"type": "string", "enum": ["list", "status", "items", "payment"]},
    }
    examples = [
        "查看我的订单",
        "我的订单发货了吗",
        "帮我查一下最近的订单",
    ]

    async def execute(self, state: dict, step: dict) -> dict:
        params = self._extract_and_validate_params(state, step)

        # 1. 权限校验：user_id 必须来自认证上下文
        user_id = state.get("cs_context", {}).get("authenticated_user_id")
        if not user_id:
            return self._error("authentication_required", "请先登录")

        # 2. 业务查询：通过 Service 层访问数据库
        try:
            result = await order_service.query_orders(
                user_id=user_id,
                order_id=params.get("order_id"),
                query_type=params.get("query_type", "list"),
            )
            return self._success(result)
        except BusinessRuleError as e:
            return self._error("business_rule", str(e))
        except DatabaseError as e:
            logger.error(f"Order query failed: {e}")
            return self._error("service_unavailable", "系统繁忙，请稍后重试")
```

### 5.3 Service 层设计

Skill 不直接操作数据库，而是通过 Service 层：

```python
# backend/customer_service/service/order_service.py
class OrderService:
    """订单查询服务 — 所有数据库访问必须经过此层"""

    async def query_orders(
        self,
        user_id: str,
        order_id: str | None = None,
        query_type: str = "list",
    ) -> OrderQueryResult:
        # 1. 参数校验
        if order_id:
            self._validate_order_id(order_id)

        # 2. 数据库查询（始终带 user_id 条件）
        async with get_business_connection() as conn:
            if order_id:
                # 关键：WHERE user_id = $1 AND order_id = $2
                row = await conn.fetchrow(
                    "SELECT * FROM \"order\".orders WHERE customer_id = $1 AND id = $2",
                    user_id, order_id,
                )
                if not row:
                    raise OrderNotFoundError(f"订单 {order_id} 不存在或不属于当前用户")
                return OrderQueryResult(order=row)
            else:
                rows = await conn.fetch(
                    "SELECT * FROM \"order\".orders WHERE customer_id = $1 ORDER BY created_at DESC LIMIT 20",
                    user_id,
                )
                return OrderQueryResult(orders=rows)

    def _validate_order_id(self, order_id: str):
        if not re.match(r'^[a-zA-Z0-9-]+$', order_id) or len(order_id) > 64:
            raise ValidationError(f"Invalid order_id format")
```

### 5.4 禁止事项

| 禁止 | 原因 | 替代方案 |
|------|------|----------|
| Skill 直接执行 SQL | 绕过业务规则 | 通过 Service 层 |
| 暴露通用 SQL Agent 给客服 | 安全风险 | 专用客服 Skill |
| LLM 生成 SQL 查询业务数据 | 不可控 | 参数化查询 |
| LLM 编造订单/物流状态 | 幻觉风险 | DB 查询结果为准 |
| 仅凭 order_id 查询 | 越权风险 | 必须绑定 user_id |
| 静默失败 (`except: pass`) | 不可追踪 | 显式错误分类 |

---

## 6. 身份与权限模型

### 6.1 身份传播链路

```
用户登录/认证
    │
    ▼
API Gateway / Frontend
    │  设置可信身份头
    ▼
FastAPI Middleware
    │  提取 user_id (X-User-Id 或 JWT)
    ▼
Route Handler
    │  注入 state["cs_context"]["authenticated_user_id"]
    ▼
CS Router → Skill → Service → DB Query
    │  每一层都携带 user_id
    ▼
SQL: WHERE customer_id = $1
```

### 6.2 权限校验层

```python
# backend/customer_service/security/permission.py

class PermissionChecker:
    """客服权限校验 — 所有业务操作必须经过此层"""

    @staticmethod
    async def check_order_access(user_id: str, order_id: str) -> OrderAccessResult:
        """检查用户是否有权访问指定订单"""
        async with get_business_connection() as conn:
            order = await conn.fetchrow(
                'SELECT id, customer_id, status FROM "order".orders WHERE id = $1',
                order_id,
            )
            if not order:
                raise OrderNotFoundError("订单不存在")
            if order["customer_id"] != user_id:
                raise AuthorizationError("无权访问此订单")
            return OrderAccessResult(order=order)

    @staticmethod
    async def check_action_permission(
        user_id: str,
        action_type: str,
        target_id: str,
    ) -> bool:
        """检查用户是否有权执行指定操作"""
        # 1. 验证目标资源属于当前用户
        # 2. 验证操作在业务规则允许范围内
        # 3. 验证资源状态允许该操作（如：已退款不可再退）
        ...

    @staticmethod
    def validate_user_identity(state: dict) -> str:
        """从 state 中提取经过认证的用户 ID"""
        cs_ctx = state.get("cs_context", {})
        user_id = cs_ctx.get("authenticated_user_id")
        if not user_id or user_id == "anonymous":
            raise AuthenticationError("用户未认证")
        return user_id
```

### 6.3 防越权矩阵

| 操作 | 需要的权限 | 验证方式 |
|------|-----------|---------|
| 查询自己的订单 | `authenticated` | `orders.customer_id = user_id` |
| 查询自己的物流 | `authenticated` + 订单归属 | 先验证订单归属 |
| 查询自己的退款 | `authenticated` + 订单归属 | 先验证订单归属 |
| 申请退款 | `authenticated` + 订单归属 + 状态允许 | 订单状态 ∈ {已付款, 已发货, 已收货} |
| 修改收货地址 | `authenticated` + 订单状态允许 | 订单状态 ∈ {待发货} |
| 查看其他用户订单 | **禁止** | 无此权限 |
| 修改其他用户订单 | **禁止** | 无此权限 |

---

## 7. 高风险操作确认机制

### 7.1 风险等级定义

| 等级 | 定义 | 操作示例 | 确认要求 |
|------|------|---------|---------|
| `low` | 只读查询 | 查订单、查物流、查政策 | 无需确认 |
| `medium` | 低风险变更 | 修改收货地址（待发货） | 需要确认 |
| `high` | 涉及资金 | 退款、退货 | 需要明确确认 + 审计 |
| `critical` | 不可逆操作 | 取消已发货订单、大额退款 | 需要二次确认 + 人工审核 |

### 7.2 确认状态机

```python
class ConfirmationState(str, Enum):
    NOT_REQUIRED = "not_required"       # 无需确认（低风险）
    PENDING_CONFIRMATION = "pending"    # 等待用户确认
    USER_CONFIRMED = "confirmed"        # 用户已确认
    USER_CANCELLED = "cancelled"        # 用户取消
    EXECUTING = "executing"             # 执行中
    SUCCESS = "success"                 # 执行成功
    FAILED = "failed"                   # 执行失败
    EXPIRED = "expired"                 # 确认超时

# 状态转换
VALID_TRANSITIONS = {
    NOT_REQUIRED: [EXECUTING],
    PENDING_CONFIRMATION: [USER_CONFIRMED, USER_CANCELLED, EXPIRED],
    USER_CONFIRMED: [EXECUTING],
    EXECUTING: [SUCCESS, FAILED],
}
```

### 7.3 确认流程

```
用户: "帮我退款"
    │
    ▼
Router: intent = create_refund, risk_level = high
    │
    ▼
Permission Check: user_id 验证 ✓, 订单归属验证 ✓
    │
    ▼
Business Rule Check:
    - 订单状态 ∈ {已付款, 已发货, 已收货} ✓
    - 未超过退货期限 ✓
    - 未重复退款 ✓
    │
    ▼
Generate Action Proposal:
    "您确定要退款吗？
     订单: #12345
     商品: 运动鞋 x1
     退款金额: ¥299.00
     退款方式: 原路返回
     请回复「确认退款」或「取消」"
    │
    ▼
Confirmation State: PENDING_CONFIRMATION
    │
    ▼ (等待用户回复)
    │
用户: "确认退款"
    │
    ▼
Confirmation State: USER_CONFIRMED → EXECUTING
    │
    ▼
Execute: refund_service.create_refund(...)
    │
    ▼
Audit Log: {action: "refund", before: "已付款", after: "退款中", ...}
    │
    ▼
Confirmation State: SUCCESS
    │
    ▼
Response: "退款申请已提交，预计 3-5 个工作日到账。"
```

### 7.4 确认超时与取消

- 确认超时：15 分钟无回复 → `EXPIRED`，清除待确认操作
- 用户取消：回复"取消"/"不退了" → `USER_CANCELLED`
- 新操作覆盖：用户发起新操作 → 旧 `PENDING_CONFIRMATION` 自动取消

### 7.5 与 Memory 的集成

确认状态存储在 `state["cs_context"]["pending_action"]` 中，同时持久化到 `conversations` 表：

```python
# 确认上下文（存储在 AgentState 扩展字段中）
cs_context = {
    "authenticated_user_id": "user_123",
    "conversation_id": "conv_456",
    "pending_action": {
        "action_type": "refund",
        "target_id": "order_789",
        "confirmation_state": "pending",
        "proposal_text": "您确定要退款吗？...",
        "created_at": "2026-09-03T10:00:00Z",
        "expires_at": "2026-09-03T10:15:00Z",
    },
    "handoff_state": "ai_active",
}
```

---

## 8. 客服 RAG 设计

### 8.1 客服知识库规划

```
kb_id                    # 对应现有 KB 注册表扩展
├── cs_faq                # 客服常见问答
├── cs_product            # 商品信息
├── cs_after_sales        # 售后政策
├── cs_logistics          # 物流政策
├── cs_promotion          # 促销活动规则
└── cs_policy             # 平台政策
```

### 8.2 KB 配置扩展

```python
# config/knowledge_base.py 新增
KNOWLEDGE_BASES["cs_faq"] = {
    "display_name": "客服FAQ",
    "content_domain": "customer_service",
    "owner_depts": ["customer_service"],
}
# ... 其他 KB 类似

# config/kb_rules.py 新增
KB_ROUTING_RULES["cs_faq"] = {
    "keywords": ["怎么", "如何", "什么是", "能不能", "可以吗"],
    "boost": 1.2,
}
```

### 8.3 Intent → KB 映射

```python
INTENT_KB_MAP = {
    # KNOWLEDGE 域
    "product_faq":        ["cs_product", "cs_faq"],
    "policy":             ["cs_policy"],
    "promotion":          ["cs_promotion"],
    "logistics_policy":   ["cs_logistics", "cs_faq"],
    "after_sales_policy": ["cs_after_sales", "cs_policy"],
}
```

### 8.4 置信度与拒答策略

```python
class CSAnswerDecision:
    """客服回答决策"""

    @staticmethod
    def decide(confidence: float, risk_level: str) -> str:
        """
        返回: answer / cautious_answer / refuse
        """
        if confidence >= 0.85:
            return "answer"                    # 正常回答

        if confidence >= 0.60:
            if risk_level in ("high", "critical"):
                return "refuse"                # 高风险场景谨慎
            return "cautious_answer"           # 谨慎回答 + 证据

        return "refuse"                        # 拒答
```

**拒答响应模板**：
```python
REFUSAL_MESSAGES = {
    "no_evidence": "抱歉，我暂时无法准确回答这个问题。建议您联系人工客服获取更详细的信息。",
    "low_confidence": "根据现有资料，我无法给您一个确定的答案。您可以尝试换个方式描述问题，或者联系人工客服。",
    "out_of_scope": "这个问题超出了我的服务范围，建议您联系人工客服获取帮助。",
    "high_risk": "这个问题涉及到您的账户安全，为了给您提供更准确的服务，建议您联系人工客服。",
}
```

### 8.5 业务事实 vs 静态知识

| 问题类型 | 数据来源 | 示例 |
|---------|---------|------|
| 实时业务事实 | DB / API | "我的订单什么时候发货？" |
| 静态知识 | RAG | "你们一般几天发货？" |
| 混合 | 两者结合 | "这个商品可以退吗？" → RAG 查政策 + DB 查订单状态 |

**关键原则**：RAG 不返回实时业务数据，DB 不返回政策知识。

---

## 9. 客服 Memory 设计

### 9.1 复用现有三层架构

| 层级 | 用途 | 客服场景 |
|------|------|---------|
| L1 Short-term | 当前对话上下文 | 解析"昨天那个鞋子"等指代 |
| L2 Session | 会话级持久化 | 会话恢复、摘要 |
| L3 Long-term | 跨会话记忆 | 用户偏好、历史问题 |

### 9.2 Memory 使用边界

```
Memory 可以做:
├── 解析上下文指代 ("那个" → 上文提到的订单)
├── 保持对话连贯性 (多轮问答)
├── 记录用户偏好 (语言偏好、沟通风格)
└── 辅助意图理解 (上文是退货话题 → 当前"好的"可能是确认退货)

Memory 不可以做:
├── 作为订单状态的事实来源 (必须查 DB)
├── 作为退款状态的事实来源 (必须查 DB)
├── 作为用户身份验证依据 (必须查认证系统)
└── 作为业务规则来源 (必须查 RAG / 配置)
```

### 9.3 user_id 激活

当前 `user_id` 在 Memory 调用链中默认为 `"default"`。客服系统需要激活 user_id 传播：

```python
# memory/manager.py 修改
def start_session(self, session_id: str, question: str, user_id: str = "default"):
    # 新增 user_id 参数，默认值保持向后兼容
    ...

# orchestration/graph/system.py 修改
def ask(self, question, session_id, kb_id="default", user_id="default"):
    # 客服场景传入真实 user_id
    self.memory_manager.start_session(session_id, question, user_id=user_id)
    ...
```

---

## 10. 人工接管设计

### 10.1 转接触发条件

```python
HANDOFF_TRIGGERS = {
    "explicit_request": {
        "patterns": [r"人工", r"真人", r"转接", r"找客服"],
        "action": "immediate_handoff",
    },
    "low_confidence": {
        "threshold": 0.50,
        "consecutive_count": 2,     # 连续 2 次低置信度
        "action": "auto_handoff",
    },
    "complaint": {
        "action": "create_ticket_then_handoff",
    },
    "high_risk_action": {
        "action": "human_review_required",
    },
    "consecutive_failures": {
        "threshold": 3,             # 连续 3 次回答失败
        "action": "auto_handoff",
    },
    "complex_after_sales": {
        "conditions": ["multiple_returns", "high_value", "disputed"],
        "action": "human_review_required",
    },
}
```

### 10.2 会话状态机

```python
class HandoffState(str, Enum):
    AI_ACTIVE = "ai_active"                 # AI 客服活跃
    HANDOFF_REQUESTED = "handoff_requested" # 已请求转人工
    WAITING_HUMAN = "waiting_human"         # 等待人工接入
    HUMAN_ACTIVE = "human_active"           # 人工客服活跃
    CLOSED = "closed"                       # 会话关闭

VALID_TRANSITIONS = {
    AI_ACTIVE: [HANDOFF_REQUESTED, CLOSED],
    HANDOFF_REQUESTED: [WAITING_HUMAN, AI_ACTIVE, CLOSED],  # 可回退到 AI
    WAITING_HUMAN: [HUMAN_ACTIVE, CLOSED],
    HUMAN_ACTIVE: [CLOSED],
}
```

### 10.3 转接流程

```
触发转接
    │
    ▼
保存当前对话上下文 (conversation + messages)
    │
    ▼
创建工单 (ticket)
    │  包含: 用户信息、对话摘要、转接原因、待处理操作
    ▼
HandoffState: HANDOFF_REQUESTED → WAITING_HUMAN
    │
    ▼
通知人工客服 (WebSocket / 消息队列)
    │
    ▼
人工客服接入 → HandoffState: HUMAN_ACTIVE
    │
    ▼
AI 停止自动回答业务问题
    │  可以辅助人工客服（提供建议、查询数据）
    ▼
人工客服处理完毕 → 可选恢复 AI (HUMAN_ACTIVE → AI_ACTIVE)
```

### 10.4 AI 在人工模式下的行为

```python
if handoff_state == HandoffState.HUMAN_ACTIVE:
    # AI 不再自动回答
    # 但可以：
    # 1. 提供知识库建议给人工客服
    # 2. 查询数据（订单、物流等）
    # 3. 记录人工客服的操作

    if user_message_is_directed_at_ai:
        # 人工客服明确要求 AI 做某事
        handle_agent_assist(...)
    else:
        # 不干预人工客服对话
        return
```

---

## 11. 投诉处理设计

### 11.1 投诉检测

```python
COMPLAINT_PATTERNS = [
    r"投诉", r"举报", r"太差了", r"服务差", r"不满意",
    r"要.*说法", r"找.*领导", r"12315", r"消协",
    r"差评", r"曝光", r"维权",
]

def detect_complaint(query: str) -> ComplaintDetection:
    """投诉检测 — 在 Router 之前执行"""
    matches = [p for p in COMPLAINT_PATTERNS if re.search(p, query)]
    if matches:
        return ComplaintDetection(
            is_complaint=True,
            severity="high" if len(matches) >= 2 else "medium",
            matched_patterns=matches,
        )
    return ComplaintDetection(is_complaint=False)
```

### 11.2 投诉处理流程

```
投诉检测命中
    │
    ▼
情绪安抚响应:
    "非常抱歉给您带来了不好的体验，我们非常重视您的反馈。
     我已经记录了您的问题，会尽快安排专人为您处理。"
    │
    ▼
创建投诉工单:
    {
        "type": "complaint",
        "user_id": "...",
        "conversation_id": "...",
        "summary": "对话摘要",
        "original_messages": [...],    # 保存完整对话上下文
        "severity": "medium",
        "status": "open",
    }
    │
    ▼
HandoffState → HANDOFF_REQUESTED
    │
    ▼
优先分配人工客服
```

---

## 12. 数据库设计

### 12.1 新增表（Business DB 扩展）

迁移文件：`backend/sql/migrations/006_customer_service.sql`

```sql
-- =============================================
-- 客服系统数据库扩展
-- 006_customer_service.sql
-- =============================================

-- 客服会话表
CREATE TABLE IF NOT EXISTS customer_service.conversations (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id VARCHAR(64) NOT NULL UNIQUE,
    user_id         VARCHAR(64) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'waiting_human', 'human_active', 'closed')),
    channel         VARCHAR(20) NOT NULL DEFAULT 'web'
                    CHECK (channel IN ('web', 'app', 'phone', 'wechat')),
    assigned_agent  VARCHAR(64),              -- 人工客服 ID
    ai_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    summary         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cs_conv_user
    ON customer_service.conversations (user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_cs_conv_status
    ON customer_service.conversations (status) WHERE status != 'closed';
CREATE INDEX IF NOT EXISTS idx_cs_conv_agent
    ON customer_service.conversations (assigned_agent)
    WHERE assigned_agent IS NOT NULL;

-- 客服消息表
CREATE TABLE IF NOT EXISTS customer_service.messages (
    id              BIGSERIAL PRIMARY KEY,
    message_id      VARCHAR(64) NOT NULL UNIQUE,
    conversation_id VARCHAR(64) NOT NULL
                    REFERENCES customer_service.conversations(conversation_id)
                    ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL
                    CHECK (role IN ('user', 'assistant', 'system', 'human_agent')),
    content         TEXT NOT NULL,
    intent_domain   VARCHAR(30),              -- Router 识别的 domain
    intent_name     VARCHAR(50),              -- Router 识别的 intent
    confidence      FLOAT,
    metadata        JSONB DEFAULT '{}',       -- 额外元数据
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cs_msg_conv
    ON customer_service.messages (conversation_id, created_at);

-- 客服操作记录表
CREATE TABLE IF NOT EXISTS customer_service.agent_actions (
    id              BIGSERIAL PRIMARY KEY,
    action_id       VARCHAR(64) NOT NULL UNIQUE,
    conversation_id VARCHAR(64) NOT NULL
                    REFERENCES customer_service.conversations(conversation_id),
    user_id         VARCHAR(64) NOT NULL,
    action_type     VARCHAR(30) NOT NULL
                    CHECK (action_type IN (
                        'refund_request', 'refund_execute',
                        'return_request', 'return_execute',
                        'address_update', 'order_cancel',
                        'complaint_create', 'handoff_request',
                        'handoff_accept', 'handoff_close'
                    )),
    target_type     VARCHAR(30),              -- order / refund / complaint 等
    target_id       VARCHAR(64),              -- 目标资源 ID
    before_state    JSONB,                    -- 操作前状态
    after_state     JSONB,                    -- 操作后状态
    confirmation_state VARCHAR(20) NOT NULL
                    CHECK (confirmation_state IN (
                        'not_required', 'pending', 'confirmed',
                        'cancelled', 'executing', 'success', 'failed', 'expired'
                    )),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'executing', 'success', 'failed', 'cancelled')),
    executed_by     VARCHAR(20) NOT NULL DEFAULT 'ai'
                    CHECK (executed_by IN ('ai', 'human', 'system')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cs_action_conv
    ON customer_service.agent_actions (conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_cs_action_user
    ON customer_service.agent_actions (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cs_action_pending
    ON customer_service.agent_actions (status)
    WHERE status IN ('pending', 'executing');

-- 审计日志表
CREATE TABLE IF NOT EXISTS customer_service.audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    log_id          VARCHAR(64) NOT NULL UNIQUE,
    user_id         VARCHAR(64) NOT NULL,
    conversation_id VARCHAR(64),
    action_id       VARCHAR(64),              -- 关联 agent_actions
    actor_type      VARCHAR(20) NOT NULL
                    CHECK (actor_type IN ('ai', 'human', 'system')),
    actor_id        VARCHAR(64),              -- 具体操作者 ID
    action          VARCHAR(50) NOT NULL,     -- 操作描述
    resource_type   VARCHAR(30),              -- 资源类型
    resource_id     VARCHAR(64),              -- 资源 ID
    before_state    JSONB,
    after_state     JSONB,
    result          VARCHAR(20) NOT NULL
                    CHECK (result IN ('success', 'failure', 'denied', 'error')),
    error_detail    TEXT,
    ip_address      VARCHAR(45),
    user_agent      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cs_audit_user
    ON customer_service.audit_logs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cs_audit_action
    ON customer_service.audit_logs (action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cs_audit_resource
    ON customer_service.audit_logs (resource_type, resource_id)
    WHERE resource_type IS NOT NULL;

-- 投诉工单表
CREATE TABLE IF NOT EXISTS customer_service.complaints (
    id              BIGSERIAL PRIMARY KEY,
    complaint_id    VARCHAR(64) NOT NULL UNIQUE,
    user_id         VARCHAR(64) NOT NULL,
    conversation_id VARCHAR(64) NOT NULL
                    REFERENCES customer_service.conversations(conversation_id),
    category        VARCHAR(30) NOT NULL
                    CHECK (category IN (
                        'service_quality', 'product_quality',
                        'logistics', 'refund_dispute', 'other'
                    )),
    severity        VARCHAR(10) NOT NULL DEFAULT 'medium'
                    CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    description     TEXT NOT NULL,
    context_summary TEXT,                     -- 对话上下文摘要
    status          VARCHAR(20) NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'investigating', 'resolved', 'escalated', 'closed')),
    assigned_to     VARCHAR(64),
    resolution      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cs_complaint_user
    ON customer_service.complaints (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cs_complaint_status
    ON customer_service.complaints (status) WHERE status NOT IN ('closed', 'resolved');

-- 确认状态表（持久化确认状态机）
CREATE TABLE IF NOT EXISTS customer_service.confirmations (
    id              BIGSERIAL PRIMARY KEY,
    confirmation_id VARCHAR(64) NOT NULL UNIQUE,
    conversation_id VARCHAR(64) NOT NULL
                    REFERENCES customer_service.conversations(conversation_id),
    user_id         VARCHAR(64) NOT NULL,
    action_type     VARCHAR(30) NOT NULL,
    target_type     VARCHAR(30) NOT NULL,
    target_id       VARCHAR(64) NOT NULL,
    proposal        JSONB NOT NULL,           -- 操作提案详情
    state           VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (state IN (
                        'pending', 'confirmed', 'cancelled',
                        'executing', 'success', 'failed', 'expired'
                    )),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,
    confirmed_at    TIMESTAMPTZ,
    executed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cs_confirm_pending
    ON customer_service.confirmations (user_id, state)
    WHERE state = 'pending';

-- 授权只读角色访问客服 schema
GRANT USAGE ON SCHEMA customer_service TO agent_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA customer_service TO agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA customer_service
    GRANT SELECT ON TABLES TO agent_readonly;
```

### 12.2 现有表扩展

```sql
-- 扩展 customer.customers 表（如需要）
ALTER TABLE customer.customers
    ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(20),
    ADD COLUMN IF NOT EXISTS auth_external_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS phone VARCHAR(20),
    ADD COLUMN IF NOT EXISTS email VARCHAR(128),
    ADD COLUMN IF NOT EXISTS default_address_id BIGINT;

-- 扩展 "order".orders 表（如需要）
ALTER TABLE "order".orders
    ADD COLUMN IF NOT EXISTS shipping_address JSONB,
    ADD COLUMN IF NOT EXISTS tracking_number VARCHAR(64),
    ADD COLUMN IF NOT EXISTS carrier VARCHAR(30),
    ADD COLUMN IF NOT EXISTS estimated_delivery TIMESTAMPTZ;

-- 扩展 "order".refunds 表
ALTER TABLE "order".refunds
    ADD COLUMN IF NOT EXISTS reason TEXT,
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'processing', 'completed', 'rejected')),
    ADD COLUMN IF NOT EXISTS approved_by VARCHAR(64),
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
```

### 12.3 Schema Config 扩展

```python
# backend/sql/data/schema_config.py 新增
DOMAINS["customer_service"] = {
    "tables": {
        "customer_service.conversations": {
            "description": "客服会话",
            "columns": ["conversation_id", "user_id", "status", "channel", ...],
        },
        "customer_service.messages": {
            "description": "客服消息",
            "columns": ["message_id", "conversation_id", "role", "content", ...],
        },
        # ... 其他表
    }
}
```

---

## 13. 客服 LangGraph 流程

### 13.1 客服专用 Graph 拓扑

在现有 LangGraph 基础上扩展，新增 `customer_service` 路由模式：

```
START
  │
  ▼
input_guard ──── BLOCK/CLARIFY ──→ guard_response → END
  │ (pass)
  ▼
identify_user ── 认证用户身份，注入 user_id
  │
  ▼
load_conversation ── 加载/创建客服会话
  │
  ▼
cs_router ── 分层 Router（粗分类 + 细分类）
  │
  ├── route_path = knowledge_query
  │       │
  │       ▼
  │     cs_rag_node ── RAG (复用现有管线，指定客服 KB)
  │       │
  │       ▼
  │     evidence_check ── 证据验证 + 拒答判断
  │       │
  │       ▼
  │     cs_response → END
  │
  ├── route_path = business_query
  │       │
  │       ▼
  │     permission_check ── 权限校验
  │       │
  │       ▼
  │     cs_skill_executor ── 执行客服 Skill (order/logistics/...)
  │       │
  │       ▼
  │     output_guard ── 输出安全检查
  │       │
  │       ▼
  │     cs_response → END
  │
  ├── route_path = business_action
  │       │
  │       ▼
  │     permission_check
  │       │
  │       ▼
  │     business_rule_check ── 业务规则校验
  │       │
  │       ▼
  │     risk_assessment ── 风险评估
  │       │
  │       ▼
  │     confirmation_gate ── 确认状态机
  │       │
  │       ├── PENDING_CONFIRMATION → ask_confirmation → END
  │       │
  │       └── USER_CONFIRMED
  │               │
  │               ▼
  │             execute_action ── 执行业务操作
  │               │
  │               ▼
  │             audit_log ── 记录审计
  │               │
  │               ▼
  │             cs_response → END
  │
  ├── route_path = complaint_flow
  │       │
  │       ▼
  │     create_complaint ── 创建投诉工单
  │       │
  │       ▼
  │     handoff_node ── 转人工
  │       │
  │       ▼
  │     cs_response → END
  │
  └── route_path = human_handoff
          │
          ▼
        handoff_node ── 转人工
          │
          ▼
        cs_response → END
```

### 13.2 Graph Builder 扩展

```python
# orchestration/graph/builder.py 扩展
def build_graph():
    graph = StateGraph(CSAgentState)

    # 现有节点（保持不变）
    graph.add_node("router", trace_wrapped(router_node))
    graph.add_node("planner", trace_wrapped(planner_node))
    # ... 其他现有节点

    # 新增客服节点
    graph.add_node("identify_user", trace_wrapped(identify_user_node))
    graph.add_node("load_conversation", trace_wrapped(load_conversation_node))
    graph.add_node("cs_router", trace_wrapped(cs_router_node))
    graph.add_node("cs_rag_node", trace_wrapped(cs_rag_node))
    graph.add_node("permission_check", trace_wrapped(permission_check_node))
    graph.add_node("cs_skill_executor", trace_wrapped(cs_skill_executor_node))
    graph.add_node("business_rule_check", trace_wrapped(business_rule_check_node))
    graph.add_node("confirmation_gate", trace_wrapped(confirmation_gate_node))
    graph.add_node("execute_action", trace_wrapped(execute_action_node))
    graph.add_node("audit_log", trace_wrapped(audit_log_node))
    graph.add_node("handoff_node", trace_wrapped(handoff_node))
    graph.add_node("create_complaint", trace_wrapped(create_complaint_node))
    graph.add_node("output_guard", trace_wrapped(output_guard_node))
    graph.add_node("cs_response", trace_wrapped(cs_response_node))

    # 条件边
    graph.add_conditional_edges("input_guard", route_after_guard, {
        "blocked": "guard_response",
        "pass": "identify_user",
    })
    graph.add_conditional_edges("cs_router", route_after_cs_router, {
        "knowledge_query": "cs_rag_node",
        "business_query": "permission_check",
        "business_action": "permission_check",
        "complaint_flow": "create_complaint",
        "human_handoff": "handoff_node",
    })
    # ... 其他条件边

    return graph.compile()
```

### 13.3 State 扩展

```python
# orchestration/state.py 扩展
class CSAgentState(AgentState):
    """客服 Agent 状态 — 扩展现有 AgentState"""
    cs_context: dict       # 客服上下文
    # cs_context 结构:
    # {
    #     "authenticated_user_id": str,
    #     "conversation_id": str,
    #     "handoff_state": str,
    #     "pending_action": dict | None,
    #     "cs_route": dict,          # CS Router 输出
    #     "confirmation_state": str,
    #     "retry_count": int,
    # }
    cs_action_result: dict  # 业务操作结果
    cs_audit_entries: list  # 审计记录
```

---

## 14. 错误处理设计

### 14.1 错误分类体系

```python
# backend/customer_service/errors.py

class CustomerServiceError(Exception):
    """客服系统基础错误"""
    def __init__(self, message: str, code: str, user_message: str | None = None):
        self.message = message
        self.code = code
        self.user_message = user_message or "系统繁忙，请稍后重试"
        super().__init__(message)

class AuthenticationError(CustomerServiceError):
    """认证失败"""
    def __init__(self, message="用户未认证"):
        super().__init__(message, "AUTH_FAILED", "请先登录后再使用此功能")

class AuthorizationError(CustomerServiceError):
    """权限不足"""
    def __init__(self, message="无权执行此操作"):
        super().__init__(message, "PERMISSION_DENIED", "您没有权限执行此操作")

class ValidationError(CustomerServiceError):
    """参数校验失败"""
    def __init__(self, message="参数校验失败"):
        super().__init__(message, "VALIDATION_ERROR", "输入信息有误，请检查后重试")

class BusinessRuleError(CustomerServiceError):
    """业务规则不满足"""
    def __init__(self, message, user_message=None):
        super().__init__(message, "BUSINESS_RULE", user_message or message)

class OrderNotFoundError(BusinessRuleError):
    def __init__(self, message="订单不存在"):
        super().__init__(message, "ORDER_NOT_FOUND", "未找到相关订单信息")

class OrderNotEligibleError(BusinessRuleError):
    def __init__(self, message):
        super().__init__(message, "ORDER_NOT_ELIGIBLE", message)

class RetrievalError(CustomerServiceError):
    """RAG 检索失败"""
    def __init__(self, message="知识检索失败"):
        super().__init__(message, "RETRIEVAL_ERROR", "暂时无法查询相关信息")

class ExternalServiceError(CustomerServiceError):
    """外部服务异常"""
    def __init__(self, message="外部服务异常"):
        super().__init__(message, "EXTERNAL_SERVICE", "系统繁忙，请稍后重试")

class DatabaseError(CustomerServiceError):
    """数据库异常"""
    def __init__(self, message="数据库异常"):
        super().__init__(message, "DATABASE_ERROR", "系统繁忙，请稍后重试")

class ActionExecutionError(CustomerServiceError):
    """操作执行失败"""
    def __init__(self, message, user_message=None):
        super().__init__(message, "ACTION_FAILED", user_message or "操作执行失败，请稍后重试")

class HumanHandoffError(CustomerServiceError):
    """人工转接异常"""
    def __init__(self, message="转接失败"):
        super().__init__(message, "HANDOFF_ERROR", "转接人工客服失败，请稍后重试")
```

### 14.2 错误处理原则

| 原则 | 实现方式 |
|------|---------|
| 不吞没错误 | 禁止 `except Exception: pass` |
| 不泄露内部信息 | `user_message` 与 `message` 分离 |
| 可追踪 | 每个错误关联 trace_id + conversation_id |
| 可恢复 | 区分可重试和不可重试错误 |
| 用户友好 | 返回中文友好提示，不暴露技术细节 |

### 14.3 错误 → 用户响应映射

```python
ERROR_USER_MESSAGES = {
    "AUTH_FAILED":       "请先登录后再使用此功能。",
    "PERMISSION_DENIED": "您没有权限执行此操作。",
    "VALIDATION_ERROR":  "输入信息有误，请检查后重试。",
    "ORDER_NOT_FOUND":   "未找到相关订单信息，请确认订单号是否正确。",
    "BUSINESS_RULE":     None,  # 使用错误自带的 user_message
    "RETRIEVAL_ERROR":   "暂时无法查询相关信息，请稍后重试。",
    "EXTERNAL_SERVICE":  "系统繁忙，请稍后重试。",
    "DATABASE_ERROR":    "系统繁忙，请稍后重试。",
    "ACTION_FAILED":     "操作执行失败，请稍后重试。",
    "HANDOFF_ERROR":     "转接人工客服失败，请稍后重试。",
}
```

---

## 15. 安全治理设计

### 15.1 Input Guard 扩展

复用现有 `security/input_guard/` 框架，新增客服专用检查：

```python
# backend/customer_service/security/input_guard.py

class CSInputGuard:
    """客服输入安全检查"""

    def check(self, query: str, cs_context: dict) -> CSInputGuardResult:
        checks = [
            self._check_format(query),           # 长度/格式
            self._check_injection(query),         # Prompt Injection
            self._check_sql_injection(query),     # SQL Injection
            self._check_scope(query),             # 越权意图
            self._check_sensitive(query),         # 敏感信息输入
            self._check_system_probe(query),      # 系统探测
        ]
        return CSInputGuardResult.aggregate(checks)

    def _check_injection(self, query: str) -> CheckResult:
        """检测 Prompt Injection"""
        patterns = [
            r"忽略(之前|上面|以上)(的|的)?(所有)?(指令|规则|设定)",
            r"(你现在|请你?)(是|作为|扮演)(一个|一名)?",
            r"(系统|system)\s*(prompt|指令|消息)",
            r"(DAN|do\s+anything\s+now)",
        ]
        ...

    def _check_scope(self, query: str) -> CheckResult:
        """检测越权意图 — 如试图操作其他用户的数据"""
        patterns = [
            r"(查|看|查?看)(一下)?(别人|其他|所有)(用户|人)(的)?.{0,4}(订单|信息|数据)",
            r"(帮我|给我)(修改|改|删除|删)(别人|其他)(的)",
        ]
        ...
```

### 15.2 Output Guard

```python
# backend/customer_service/security/output_guard.py

class CSOutputGuard:
    """客服输出安全检查"""

    def check(self, response: str, cs_context: dict) -> str:
        """过滤输出中的敏感信息"""
        response = self._mask_other_user_info(response, cs_context)
        response = self._remove_internal_info(response)
        response = self._remove_uncommitted_promises(response)
        return response

    def _mask_other_user_info(self, response: str, ctx: dict) -> str:
        """确保不泄露其他用户的信息"""
        # 检查响应中是否包含非当前用户的姓名、手机号、地址等
        ...

    def _remove_internal_info(self, response: str) -> str:
        """移除内部信息"""
        patterns = [
            r"SELECT\s+.*\s+FROM",              # SQL 语句
            r"/[a-z_/]+\.py",                    # 系统路径
            r"Traceback \(most recent",          # 错误堆栈
            r"(API_KEY|SECRET|PASSWORD)\s*=",    # 配置信息
            r"<!--.*?-->",                        # HTML 注释（可能含 META）
        ]
        ...

    def _remove_uncommitted_promises(self, response: str) -> str:
        """移除未经证实的承诺"""
        patterns = [
            r"(我们(一定|保证|承诺))(会|将)",
            r"(赔偿|补偿).*\d+",                  # 未授权的赔偿承诺
        ]
        ...
```

### 15.3 安全检查矩阵

| 检查点 | 输入检查 | 输出检查 |
|--------|---------|---------|
| Prompt Injection | 检测 + 拦截 | - |
| SQL Injection | 检测 + 拦截 | 检测 + 移除 |
| 越权意图 | 检测 + 拦截 | - |
| 敏感信息 | 输入脱敏 | 输出脱敏 |
| 系统 Prompt 探测 | 检测 + 拦截 | 检测 + 移除 |
| 内部路径/堆栈 | - | 检测 + 移除 |
| 其他用户信息 | - | 检测 + 移除 |
| 未授权承诺 | - | 检测 + 移除 |

---

## 16. 测试方案

### 16.1 测试分层

```
测试金字塔:
                    ╱  E2E  ╲              集成测试 (少量)
                   ╱──────────╲
                  ╱ Integration╲           Skill + Service 集成
                 ╱──────────────╲
                ╱   Component    ╲         单模块测试
               ╱──────────────────╲
              ╱      Unit Tests     ╲       基础逻辑测试
             ╱────────────────────────╲
```

### 16.2 Router 测试

```python
# tests/customer_service/test_coarse_router.py
class TestCoarseRouter:
    def test_human_intent_detected(self):
        """用户要求人工 → domain=HUMAN, conf≥0.9"""

    def test_complaint_detected(self):
        """投诉意图 → domain=COMPLAINT"""

    def test_order_query_detected(self):
        """订单查询 → domain=TRANSACTION"""

    def test_refund_detected(self):
        """退款意图 → domain=AFTER_SALES"""

    def test_knowledge_query_detected(self):
        """知识问答 → domain=KNOWLEDGE"""

    def test_non_cs_query_not_detected(self):
        """非客服问题 → is_cs_domain=False"""

    def test_ambiguous_query_fallback(self):
        """模糊问题 → 低置信度，可能触发 LLM 兜底"""

# tests/customer_service/test_fine_router.py
class TestFineRouter:
    def test_order_status_vs_order_items(self):
        """区分查状态 vs 查明细"""

    def test_refund_eligibility_vs_create_refund(self):
        """区分查资格 vs 申请退款"""
```

### 16.3 权限测试

```python
# tests/customer_service/test_permission.py
class TestPermission:
    def test_user_can_query_own_order(self):
        """用户可以查询自己的订单"""

    def test_user_cannot_query_other_order(self):
        """用户不能查询他人订单 — 关键安全测试"""

    def test_user_cannot_modify_other_order(self):
        """用户不能修改他人订单"""

    def test_unauthenticated_rejected(self):
        """未认证用户被拒绝"""

    def test_order_id_without_user_id_rejected(self):
        """仅提供 order_id 不提供 user_id → 拒绝"""
```

### 16.4 业务操作测试

```python
# tests/customer_service/test_refund_skill.py
class TestRefundSkill:
    def test_eligible_order_can_refund(self):
        """符合退款条件的订单可以退款"""

    def test_ineligible_order_rejected(self):
        """不符合条件的订单被拒绝"""

    def test_duplicate_refund_rejected(self):
        """重复退款被拒绝"""

    def test_already_refunded_rejected(self):
        """已退款的订单不能再次退款"""

    def test_nonexistent_order_rejected(self):
        """不存在的订单被拒绝"""

    def test_refund_requires_confirmation(self):
        """退款需要用户确认"""

    def test_user_cancel_refund(self):
        """用户可以取消退款"""

    def test_other_user_order_cannot_refund(self):
        """不能为他人订单退款 — 关键安全测试"""

# tests/customer_service/test_confirmation.py
class TestConfirmationStateMachine:
    def test_pending_to_confirmed(self):
        """PENDING → CONFIRMED 合法转换"""

    def test_pending_to_cancelled(self):
        """PENDING → CANCELLED 合法转换"""

    def test_confirmed_to_executing(self):
        """CONFIRMED → EXECUTING 合法转换"""

    def test_pending_to_executing_illegal(self):
        """PENDING → EXECUTING 非法 — 跳过确认"""

    def test_expired_confirmation(self):
        """超时的确认自动过期"""
```

### 16.5 RAG 测试

```python
# tests/customer_service/test_rag_customer.py
class TestCustomerRAG:
    def test_faq_answered_correctly(self):
        """FAQ 问题正确回答"""

    def test_vague_question_handled(self):
        """模糊问题处理"""

    def test_no_evidence_refusal(self):
        """知识库不存在 → 拒答"""

    def test_conflicting_evidence(self):
        """冲突知识 → 谨慎回答或拒答"""

    def test_multi_turn_context(self):
        """多轮对话上下文理解"""

    def test_cross_document_answer(self):
        """跨文档回答"""

    def test_citation_present(self):
        """回答必须附带引用"""

    def test_low_confidence_refusal(self):
        """低置信度 → 拒答"""
```

### 16.6 Safety 测试

```python
# tests/customer_service/test_input_guard.py
class TestCSInputGuard:
    def test_prompt_injection_blocked(self):
        """Prompt Injection 被拦截"""

    def test_sql_injection_blocked(self):
        """SQL Injection 被拦截"""

    def test_scope_violation_blocked(self):
        """越权查询被拦截"""

    def test_sensitive_info_request_blocked(self):
        """请求敏感信息被拦截"""

    def test_system_prompt_probe_blocked(self):
        """系统 Prompt 探测被拦截"""

    def test_normal_query_passes(self):
        """正常查询通过"""

# tests/customer_service/test_output_guard.py
class TestCSOutputGuard:
    def test_no_sql_in_output(self):
        """输出不包含 SQL"""

    def test_no_internal_paths(self):
        """输出不包含系统路径"""

    def test_no_other_user_info(self):
        """输出不包含其他用户信息"""

    def test_no_stack_traces(self):
        """输出不包含错误堆栈"""
```

### 16.7 Human Handoff 测试

```python
# tests/customer_service/test_human_handoff.py
class TestHumanHandoff:
    def test_explicit_request_triggers_handoff(self):
        """用户明确要求 → 转人工"""

    def test_low_confidence_triggers_handoff(self):
        """连续低置信度 → 自动转人工"""

    def test_consecutive_failures_triggers_handoff(self):
        """连续失败 → 自动转人工"""

    def test_complaint_triggers_handoff(self):
        """投诉 → 创建工单 + 转人工"""

    def test_high_risk_triggers_handoff(self):
        """高风险操作 → 人工审核"""

    def test_ai_stops_after_handoff(self):
        """转人工后 AI 不再自动回答"""

    def test_handoff_state_transitions(self):
        """转接状态机转换正确性"""
```

### 16.8 集成测试

```python
# tests/customer_service/test_integration.py
class TestCustomerServiceIntegration:
    def test_full_knowledge_query_flow(self):
        """完整知识问答流程"""

    def test_full_order_query_flow(self):
        """完整订单查询流程"""

    def test_full_refund_flow_with_confirmation(self):
        """完整退款流程（含确认）"""

    def test_full_complaint_flow(self):
        """完整投诉流程"""

    def test_existing_agent_not_broken(self):
        """现有 Agent 功能不受影响 — 回归测试"""

    def test_existing_rag_not_broken(self):
        """现有 RAG 功能不受影响 — 回归测试"""

    def test_existing_sql_not_broken(self):
        """现有 SQL Agent 不受影响 — 回归测试"""
```

---

## 17. 评估指标

### 17.1 核心指标（必须达标）

| 指标 | 目标 | 说明 |
|------|------|------|
| Permission Violation Rate | **= 0** | 权限违规率必须为零 |
| 高风险业务误操作率 | **≈ 0** | 不允许为了效率牺牲安全 |
| Intent Routing Accuracy | ≥ 90% | 意图路由准确率 |
| Answer Faithfulness | ≥ 95% | 回答忠实度 |

### 17.2 质量指标

| 指标 | 目标 | 说明 |
|------|------|------|
| RAG Recall | ≥ 85% | 知识库召回率 |
| RAG Precision | ≥ 90% | 知识库精确率 |
| Citation Accuracy | ≥ 95% | 引用准确率 |
| Hallucination Rate | ≤ 2% | 幻觉率 |
| Refusal Accuracy | ≥ 90% | 拒答准确率（该拒则拒） |
| Business Query Accuracy | ≥ 98% | 业务查询准确率 |

### 17.3 体验指标

| 指标 | 目标 | 说明 |
|------|------|------|
| Auto Resolution Rate | ≥ 70% | 自动解决率 |
| Human Handoff Rate | ≤ 30% | 人工转接率 |
| Average Response Time | ≤ 5s | 平均响应时间 |
| User Satisfaction | ≥ 4.0/5.0 | 用户满意度 |

### 17.4 运营指标

| 指标 | 目标 | 说明 |
|------|------|------|
| Action Success Rate | ≥ 95% | 操作成功率 |
| Action Error Rate | ≤ 1% | 操作错误率 |
| Audit Coverage | = 100% | 审计覆盖率 |
| Silent Failure Rate | = 0 | 静默失败率 |

### 17.5 Prometheus 指标扩展

```python
# backend/observability/metrics.py 新增
cs_intent_total = Counter(
    "cs_intent_total", "Customer service intent routing",
    ["domain", "intent", "correct"]
)
cs_permission_violation_total = Counter(
    "cs_permission_violation_total", "Permission violation attempts",
    ["violation_type"]
)
cs_action_total = Counter(
    "cs_action_total", "Business action executions",
    ["action_type", "result"]
)
cs_confirmation_total = Counter(
    "cs_confirmation_total", "Confirmation state transitions",
    ["from_state", "to_state"]
)
cs_handoff_total = Counter(
    "cs_handoff_total", "Human handoff events",
    ["trigger_type"]
)
cs_rag_status = Counter(
    "cs_rag_status", "Customer service RAG outcomes",
    ["status"]  # hit/rejected/refused
)
```

---

## 18. 架构冲突与风险分析

### 18.1 已识别的冲突

| 冲突 | 严重性 | 解决方案 |
|------|--------|---------|
| 现有 Router 无客服意图 | 中 | 新增 Domain Detector 作为前置层，不修改现有 Router 核心逻辑 |
| `user_id` 未激活 | 高 | 扩展 Memory 调用链增加 user_id 参数，默认值保持向后兼容 |
| 无用户认证系统 | 高 | Phase 1 使用 `X-User-Id` 可信头；Phase 2 引入 JWT/OAuth |
| SQL Agent 可能执行任意 SELECT | 中 | 客服系统不暴露 SQL Agent，通过 Service 层参数化查询 |
| AgentState 扩展 | 低 | TypedDict 新增字段，向后兼容 |
| 现有 KB 无客服域 | 低 | 扩展 KNOWLEDGE_BASES 注册表 |

### 18.2 已识别的风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| LLM 编造业务数据 | 高 | 业务数据必须来自 DB，RAG 仅用于静态知识 |
| 越权操作 | 高 | 每层权限校验 + user_id 绑定 + 负向测试 |
| 确认绕过 | 高 | 状态机强制 + 持久化 + 审计 |
| Prompt Injection | 中 | Input Guard + Output Guard 双重防护 |
| 客服域误判 | 中 | Domain Detector 保守策略（不确定则走现有 Router） |
| 性能退化 | 低 | 客服 Router 独立，不影响现有请求路径 |

### 18.3 最小改造原则

所有修改遵循最小改造原则：
1. **不修改**现有 Router/Planner/Supervisor 核心逻辑
2. **扩展**而非替换：新增客服节点、能力、KB
3. **隔离**客服域：通过 `route_mode = "customer_service"` 隔离
4. **向后兼容**：所有新增参数有默认值

---

## 19. 实施路线图

### Phase 1: 基础设施（Week 1-2）

- [ ] 数据库迁移 `006_customer_service.sql`
- [ ] 客服错误分类体系 `customer_service/errors.py`
- [ ] 客服 State 扩展 `orchestration/state.py`
- [ ] 客服配置模块 `config/customer_service.py`
- [ ] user_id 传播链路激活
- [ ] 基础单元测试框架

### Phase 2: Router + 知识问答（Week 3-4）

- [ ] Domain Detector
- [ ] 粗分类 Router + 测试
- [ ] 细分类 Router + 测试
- [ ] 客服 KB 创建 + 文档上传
- [ ] KB 路由映射
- [ ] 知识问答路径集成测试

### Phase 3: 业务查询（Week 5-6）

- [ ] 权限校验层
- [ ] Order Service + Skill
- [ ] Logistics Service + Skill
- [ ] Account Service + Skill
- [ ] Output Guard
- [ ] 业务查询集成测试

### Phase 4: 业务操作 + 确认（Week 7-8）

- [ ] 确认状态机
- [ ] Refund Service + Skill
- [ ] After-sales Service + Skill
- [ ] 审计日志
- [ ] 高风险操作集成测试

### Phase 5: 安全 + 转接（Week 9-10）

- [ ] Input Guard 扩展
- [ ] Output Guard 完善
- [ ] 投诉处理流程
- [ ] 人工转接管理
- [ ] 全链路安全测试

### Phase 6: 集成 + 评估（Week 11-12）

- [ ] 端到端集成测试
- [ ] 回归测试（确保现有功能不受影响）
- [ ] 性能测试
- [ ] 评估指标收集
- [ ] 文档完善

---

## 附录 A: 文件变更清单

### 修改的文件

| 文件 | 修改内容 | 原因 |
|------|---------|------|
| `orchestration/state.py` | 新增 CSAgentState | 客服状态扩展 |
| `orchestration/graph/builder.py` | 新增客服节点和条件边 | 客服流程 |
| `orchestration/graph/router_node.py` | 新增客服域检测 | 路由分流 |
| `orchestration/router/types.py` | 新增客服 capabilities | 能力注册 |
| `orchestration/router/rule_router.py` | 新增客服规则 | 路由规则 |
| `config/knowledge_base.py` | 新增客服 KB | 知识库扩展 |
| `config/kb_rules.py` | 新增客服路由规则 | KB 路由 |
| `config/__init__.py` | 新增客服配置 | 配置管理 |
| `memory/manager.py` | 新增 user_id 参数 | 多用户支持 |
| `memory/service.py` | 传递 user_id | 多用户支持 |
| `sql/data/schema_config.py` | 注册客服表 | SQL 白名单 |
| `sql/row_security.py` | 新增客服行安全规则 | 数据安全 |
| `app/api/router.py` | 挂载客服路由 | API 注册 |
| `app/api/schemas.py` | 新增客服请求/响应模型 | API 契约 |
| `observability/metrics.py` | 新增客服指标 | 可观测性 |
| `skills/registry.py` | import 客服 skills | 技能注册 |

### 新增的文件

见 [§2.3 全新新增](#23-全新新增) 和 [§12.1 新增表](#121-新增表business-db-扩展)。

---

## 附录 B: 关键设计决策记录

### ADR-CS-001: 分层 Router 而非扩展现有 Router

**决策**：在现有 Router 前增加 Domain Detector + CS Router，而非将客服意图加入现有 12 个 capability。

**原因**：
1. 客服意图数量多（20+），混合会增加现有 Router 复杂度
2. 客服需要结构化输出（risk_level, requires_auth 等），现有 RouteDecision 不包含
3. 隔离客服路由逻辑，降低对现有功能的影响

### ADR-CS-002: Service 层而非直接 SQL

**决策**：客服 Skill 通过 Service 层访问数据库，不暴露 SQL Agent。

**原因**：
1. SQL Agent 设计为通用分析工具，非业务操作接口
2. Service 层可以实施业务规则、权限校验、参数验证
3. 参数化查询比 NL2SQL 更安全可控

### ADR-CS-003: 确认状态机持久化

**决策**：确认状态同时存储在 AgentState（内存）和数据库。

**原因**：
1. AgentState 用于当前请求的快速判断
2. 数据库持久化用于跨请求恢复（用户可能关闭页面再回来）
3. 审计需要持久化记录

### ADR-CS-004: 客服 KB 独立于现有 KB

**决策**：客服知识库使用独立 kb_id（cs_faq, cs_product 等），不复用现有 7 个 KB。

**原因**：
1. 客服知识有不同的更新频率和治理流程
2. 避免客服文档影响现有 RAG 检索质量
3. 便于独立评估和优化
