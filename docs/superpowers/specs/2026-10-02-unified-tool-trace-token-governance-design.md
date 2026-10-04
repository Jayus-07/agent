# 全平台 Tool Trace 与 Token 治理设计

## 目标

统一主 Agent、旅游域、Workflow、MCP 和外部 Provider 的 Tool 执行观测口径，确保同一次 Tool 调用同时进入运行统计、请求 Trace 和 Tool 治理详情；补齐旅游 REST/SSE 请求级 Trace，并让 Token 消耗可以按请求、模型、Provider、Skill、Tool 和业务域追溯。

## 现状问题

- `BaseSkill` 已创建 `tool_call` Span，但旅游 `live_search_service` 直调 Tool 只写 `record_tool_result`。
- 旅游 `/travel/plan` 与 `/travel/plan/stream` 没有创建统一请求 Trace。
- Workflow 中的部分直接 Tool 调用经过治理指标，但没有完整 Tool Span。
- 管理端 `/admin/tools/errors` 只读 Span 的 events/errors，遗漏写入 metrics 的错误。
- 运行监控页的 Agent Trace 数据源固定过滤 `workflow_name=agent`，Tool 下钻不能覆盖其他交互入口。
- Token 观测应使用既有 `llm_usage` 与 Trace 关联口径，不能新造第二套成本数据源。

## 设计

### 统一 Trace

所有真实 Tool 执行必须产生一个 `type=tool_call` 的 Span，输入字段统一为：

```json
{
  "tool": "travel_train_search_tool",
  "capability": "travel.train.search",
  "agent": "planning",
  "params": {"from_station": "福州", "to_station": "厦门"}
}
```

结果字段统一写入 Span：

- `status`: success / error / skipped / degraded
- `metrics.error`、`metrics.error_code`、`metrics.error_class`
- `metrics.elapsed_s`、`metrics.retries`、`metrics.contract_hash`
- 失败同时追加 `tool.error` event，供旧数据和实时监听消费

已有 BaseSkill Span 继续复用，统一执行器在没有外部 Span 时自动创建，避免重复 Span。

### 旅游请求 Trace

旅游 `/plan` 和 `/plan/stream` 创建 `workflow_name=agent` 的请求 Trace；SSE worker 在线程内显式 bind Trace。Trace tags 保留旅游状态、run_id、目的地和校验结果，原有业务逻辑不变。

### 监控下钻

普通问答 Trace 保持现有 Agent 口径；当带 `has_tool` 下钻时不限制 workflow_name，保证主 Agent、旅游 REST/SSE、Workflow 都能查到对应 Tool。旅游请求归入 `agent` workflow，因此也会出现在常规问答追踪列表。

### Token 消耗

沿用既有 `llm_usage` 记录和 Trace 关联，不新增平行成本表。每次 LLM 调用与请求 Trace 关联，管理端继续按模型、Provider、Skill、Tool/业务域读取已有 usage 字段；Tool Span 只记录调用链与契约，不把 Token 重复计入 Tool 成本。

### 强制失败测试

管理端新增管理员本地测试入口。接口只生成模拟治理指标和 Trace，不调用真实上游；生产环境拒绝执行，Tool 名必须来自契约 lock。支持统一七类错误，返回 Trace ID，页面自动刷新统计并可跳转运行监控。

## 验收标准

1. 旅游真实规划在运行监控中可见，并能按 `travel_train_search_tool` 下钻。
2. 旅游 Tool 成功、失败均有 Tool Span，失败详情能显示错误分类、错误文本和 Trace。
3. Workflow 直接 Tool 调用不再只出现在统计、不出现在 Trace。
4. 管理端强制失败入口可以生成至少 timeout、network_error、business_error 三类记录。
5. Token 明细仍来自既有 llm_usage，Trace 详情可以追溯模型、Provider、Token 和成本。
6. 生产环境不能调用强制失败接口。
