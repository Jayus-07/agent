# Architecture Simplification — STOP B/C Final Closure

日期：2026-09-29  
范围：仅关闭 STOP B Router Trace Metadata、diff check 与 Travel evaluation 验收缺口。  
（更正：本文初稿把 STOP C 一并写成 PASS，属提前结案。STOP C 的 Capability
metadata SSOT 当日另行实施，由 `2026-09-29-STOP_C-Capability-SSOT-Closure.md`
单独结案，本文件不覆盖该范围。）

## 1. 原验收缺口

STOP B/C 原有 Router 连续性测试证明了决策快照会写入 LangGraph state，但未证明
生产 `TraceRecord.metadata` 可查询这些决策。另有 STOP B 预实施审计文档的 EOF 空行，
以及尚未完成的 Travel 金标评测。

## 2. 修复内容

1. 新增 `backend/orchestration/router/router_trace.py`。它只把低基数 Router 决策投影为
   `trace.metadata["router"]`，不记录 query、prompt、reasoning、候选列表或其它大对象。
2. `router_node._with_router_decisions()` 继续原样写入
   `domain_decision`、`capability_decision`、`execution_decision` state 字段，再写入同一条
   current trace 的 metadata。Trace 不存在或观测写入异常时静默降级，不改变 state 或路由。
3. 新增真实 current-trace 测试：以 legacy `sql.query` 决策调用 Router 写回，断言
   `TraceRecord.metadata["router"]` 的完整脱敏投影。
4. 移除 `STOP_B_Pre_Implementation_Audit.md` 的 EOF 空行。

Router metadata 结构为：

```json
{
  "router": {
    "domain": "unknown",
    "subflow": null,
    "capability": "sql.query",
    "mode": "direct",
    "confidence": 0.93,
    "source": "legacy"
  }
}
```

## 3. 兼容性边界

本次未修改 LangGraph node ID、checkpoint schema、SSE 帧、`route_mode`、Router 决策逻辑、
TraceStore 或 LLM usage 关联链。metadata 复用 GraphRunner 已建立的 current trace；
TraceCollector 收尾后仍由既有 trace writer 写入 PostgreSQL trace store。

## 4. 新鲜验证结果

| 范围 | 命令/结果 |
|---|---|
| Router Trace metadata | 新增测试 RED：缺 `metadata.router` 的 `KeyError`；GREEN：`1 passed` |
| STOP B/C Router 回归 | `160 passed in 23.63s` |
| CS evaluation | `2 passed` |
| SQL evaluation | `14 passed` |
| RAG evaluation 契约 | `16 passed` |
| Travel evaluation | `2 passed in 139.96s` |
| Runtime trace/token/checkpoint/node compatibility | `40 passed in 17.24s` |

CS/SQL/RAG 组合命令总计 `32 passed`。其中 CS collection 有既存的
`PytestCollectionWarning`（Pydantic `TestCase` 具构造器），不影响用例结果。

Travel 金标运行无逐例输出，两个断言共享 34 条用例的一次执行。当前环境启用了
PostgreSQL checkpointer，因此总时长约 140 秒；本次已等待其真实完成，不以超时推断
结果。

## 5. Diff check

对本次 STOP B/C 文件范围执行
`git -c core.autocrlf=false -c core.safecrlf=false diff --check --no-ext-diff -- <STOP B/C paths>`，
无 whitespace 输出、退出码为 0。完整工作树仍含用户已有前端配置改动，普通
`git diff --check` 会为这些文件打印 Windows CRLF 转换警告；这不是 whitespace
诊断，且本次没有修改它们或 Git 配置。

## 6. 最终 Gate

```ini
ROUTER_TRACE_METADATA_PASS=true
DIFF_CHECK_PASS=true
TRAVEL_EVALUATION_VERIFIED=true
ROUTER_COMPATIBILITY_PASS=true
STOP_B_PASS=true
STOP_C_PASS=false  # 本文件不含 STOP C 范围；见 2026-09-29-STOP_C-Capability-SSOT-Closure.md
```
