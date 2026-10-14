# Trace / Span 模型

## 结构

```
Trace
└── Span（树形）
    └── Event
    └── Metric
```

## Span 必填字段

- id
- parent_id
- type
- name
- start_time
- end_time
- duration_ms
- status

## 常见 Span Type

- `llm` / `llm_call`
- `retrieval`
- `rerank`
- `sql`
- `tool` / `tool_call`
- `skill`
- `planner`
- `reporter`
- `memory`
- `agent`
- `http`

## Tool 调用记录（必填）

- trace_id
- span_id
- input
- output
- duration_ms
- model
- prompt_tokens
- completion_tokens
- error
- retry_count


## 访问控制与数据范围（2026-10-10 管理端/API 主题）

Trace 含 prompt/answer、用户问题、session/user 标识与用量信息，属敏感数据。
读取受**两层**约束，后端为权威判定、网关为外层硬闸：

| 层 | 位置 | 规则 |
| --- | --- | --- |
| 后端身份闸 | `observability.py::_require_trace_reader` → `deps.require_user_actor` | 仅 JWT 用户身份（`kind == "user"`）。API Key / 内部令牌属服务间凭据，不映射用户身份，不得读取 |
| 后端资源级 | `routes/_trace_authz.py::trace_visible_to` | 按 Trace 归属标签（`tags.tenant_id` / `tags.user_id`）逐条判定 |
| 网关 | `apisix/plugins/gateway-auth.lua` `ROLE_GATE_PREFIXES` | `/api/observability/traces` 前缀 `read/write = admin` |

### 租户与主体规则

- 归属标签由 `tracer.py` 在 `start()` 时从**权威请求上下文**写入，不消费客户端自报字段。
- Trace 未声明 `tenant_id`：仅 `super_admin` 可读。空租户**不得**降级为共享租户。
- 请求方未声明 `tenant_id`：拒绝（无法证明归属）。
- 租户不匹配：仅 `super_admin` 可跨租户；**`admin` 同样不得跨租户**。
- 同租户内 Trace 声明了 `user_id`：本人或平台管理员可读。
- 越权统一返回 **404**（而非 403），避免以状态码差异泄露「该 Trace 存在」。

### 管理端列表与统计

- `GET /api/observability/traces` 在服务端逐条执行同一资源/租户授权，再应用 `since`、`until`、`status` 等筛选和 `limit`/`offset` 分页；响应 `total` 只计授权且匹配的 Trace。
- 列表和统计均复用唯一 TraceStore。当前 TraceStore 最多保留 5,000 条；统计响应返回 `records_considered`、`retention_limit` 与 `observed_at`，仅代表该留存范围内的授权结果，不代表全量历史。
- 管理端运行总览最近记录表兼容最多展示 200 条样本；统计由 `GET /api/observability/traces/stats` 在服务端按当前身份和租户范围聚合。样本行数不能作为全量统计口径。
- CSV 导出只覆盖当前服务端分页返回的安全字段，不包含 Trace 输入/响应正文或 Session 标识。

### 子 Trace（children_ids）

- 详情接口按既有 `parent_id` 关系补齐 `children_ids`，用于发现父请求超时/断连后
  仍未回传 ID、但已落库的远端子 Trace。
- **每个子 Trace 独立执行资源级授权**：可读父 Trace 不等于可读其子 Trace；
  无权子 Trace 既不进入 `children_ids`，也不作为继续下钻的跳板。
- 存储层查询强制带租户作用域（`list_children(..., tenant_id=...)`）；未声明租户时
  不发起无作用域查询。
- 递归有上限（`MAX_CHILD_DEPTH = 3`，每层 `MAX_CHILDREN_PER_NODE = 50`），并以
  `seen` 集合去重，父子互指/自环不会死循环。子查询失败软降级，不影响父 Trace 返回。
- 前端（详情页）同样以 `seen` 去重并限制总量（100）与批大小（25）。

### 字段脱敏

- `backend/observability/redaction.py` 是既有脱敏出口，按 `TRACE_DETAIL_LEVEL` 生效：
  `full`（默认，不脱敏）/ `masked`（PII 掩码）/ `summary`（掩码 + 剥离 span body）。
- 注意：默认 `full` 档下读取路径不做脱敏，因此**写入侧不得落用户原文**这一约束
  仍然成立——SQL 的两处含 `question[:500]` 的埋点继续不纳入集成树。

## 当前实现（更正）

- Tracer: `backend/observability/tracer.py`（`TraceCollector`；contextvar + thread 双轨）
- 持久化: `backend/observability/trace_store_pg.py`（PostgreSQL；SQLite 轨已于 2026-09-17 删除）
- API: `backend/app/api/routes/observability.py`；DTO: `routes/_trace_dto.py`
- 授权: `backend/app/api/routes/_trace_authz.py`

## 开发时的 Span 生命周期与归因门禁

本节是**跨服务开发约束**，不是所有旧 Span 字段现状的证明。入口、Router、LangGraph、Domain、Skill/Tool、LLM、RAG/SQL 和异步任务各自使用现有 Trace 接口，不能为某个域创建第二套不可关联的 Tracer。

- 请求/任务创建 trace 时绑定由服务端验证的身份和归属；子请求/子图/任务用已有 parent、trace_id、span_id 关系传播，不能由浏览器或外部 Tool 回写别人的归属标签。
- Span 至少明确开始、结束、真实耗时、执行状态、错误类别与合理的父子关系；异常、取消、超时和 fallback 同样必须收口。取消事件与业务副作用最终结果不同，不能当成功。
- Router 要能解释选择层/候选目标；Tool 要能区分执行状态、业务状态和 DEGRADED；LLM 调用关联模型、prompt key/version、token/成本及成本事实状态。**字段名遵循现有 schema/埋点，不据本文硬增表。**
- 同一请求可以调用同一 prompt key 多次；应在**调用级或节点级**追溯真实版本。现有 `backend/observability/prompt_trace.py` 的 trace-level `prompt_versions` 按 key 更新条目，不能仅凭这个汇总列表推断同一次 trace 中每次调用都用了同一版本；必要时审查具体调用侧追踪。
- 当前 `backend/config/observability.py` 默认 `TRACE_DETAIL_LEVEL=full`；`backend/observability/redaction.py` 的掩码异常分支可能返回原文。这是必须审查的敏感数据边界，**不得把配置存在当作已安全**。任何写入/展示 prompt、question、SQL 或 token 计费身份的链路均须核对授权、最小采集、留存与脱敏失败策略。
- Prometheus 做低基数聚合与告警；敏感逐请求明细保留在受控 Trace/用量 DB。Grafana 与 Trace 关联方式参见 [Grafana 规范](grafana-guide.md)。

### 变更验收

| 编号 | 必测情形 |
| --- | --- |
| TR-01 | 正常和失败各有完整父子 Span，无孤儿/悬挂 |
| TR-02 | timeout/abort/fallback 与 Tool 业务失败不被记为同一种 success |
| TR-03 | Prompt 发布/回滚期间同 key 多次调用可归因实际版本 |
| TR-04 | model/provider/token/价格版本/估算状态与账单事实一致 |
| TR-05 | Trace 列表、分页总数、统计、详情、children 下钻越权及跨租户不可见 |
| TR-06 | PII 脱敏开关、异常分支、日志与管理端不泄露敏感字段 |
| TR-07 | 断流或异步跨进程能根据父子/请求标识找回执行证据 |

执行 T1/T2 定向验证，特别关注 `backend/tests/api/test_observability_trace_authz.py`、`backend/tests/observability/` 及对应真实跨层入口。浏览器页面打开不等于所有调用链 Trace 均可追溯。
