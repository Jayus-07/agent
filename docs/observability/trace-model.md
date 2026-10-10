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
