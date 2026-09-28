# STOP C Cross-Domain Routing Continuity Report

日期：2026-09-29
范围：冻结 STOP B 后的连续会话、跨域切换、模糊指代与 fallback 验证

## 1. 背景

STOP B 已完成 Router 决策对象统一与 Evaluation Runtime 隔离。本阶段不继续做 Router 架构重构，而是验证连续用户行为下：

- domain 不会因上一轮上下文错误锁死或漂移；
- capability 与 execution mode 能承接同域任务；
- 新域强信号可以正常切换；
- 模糊短句优先走 ContinuationResolver，异常时 legacy 路径仍可用；
- 新决策快照可序列化，且不覆盖旧 state 字段。

## 2. STOP B 冻结基线

冻结记录见 [`STOP_B_ROUTER_CONSOLIDATION_FROZEN.md`](STOP_B_ROUTER_CONSOLIDATION_FROZEN.md)。

```ini
STOP_B_ROUTER_CONSOLIDATION_FROZEN=true
BASELINE_COMMIT=6ed42b780c174dc67b0a1b5a9ef2ba85491a1a75
ROUTER_NODE_ID_CHANGED=false
STATE_CONTRACT_CHANGED=false
SSE_CHANGED=false
CHECKPOINT_CHANGED=false
LEGACY_FALLBACK_AVAILABLE=true
```

STOP C 未触碰生产 Router、RAG、权限、LangGraph node id、SSE 或 checkpoint 实现。

## 3. 测试矩阵

测试文件：[`backend/tests/router/test_cross_domain_continuity.py`](../backend/tests/router/test_cross_domain_continuity.py)

| 组别 | 覆盖内容 | 用例数 | 结果 |
|---|---|---:|---:|
| A | 客服同域连续承接：订单、退款、物流、人工、序数指代 | 5 | 5/5 |
| B | 旅游同域连续规划：预算、节奏、天数、酒店、继续 | 5 | 5/5 |
| C | 客服/旅游/数据/选品跨域切换与摘要共存 | 5 | 5/5 |
| D | 模糊指代、强新域信号、显式订单实体、adapter fallback、legacy 快照 | 6 | 6/6 |
| **合计** |  | **21** | **21/21** |

矩阵通过 `router_node._with_router_decisions()`、`ContinuationResolver` 与客服实体解析器验证真实生产适配边界；分类器/embedding 采用已有 hierarchical metadata 入口，以避免把外部模型可用性混入连续性契约。域图入口使用既有 `route_mode=travel` prefilter 更新，不直接运行域图副作用。

## 4. 场景结果

### A. 同域保持

5 组客服两轮交互均保持 `domain=customer_service`、`capability=customer_service`、`execution.mode=direct`。上一轮结构化 `order_id` 被保留在 `routing_context.brief_summary`，没有回退到 `general`。

### B. 旅游连续规划

5 组旅游两轮交互均保持 `domain=travel`，并进入 `execution.mode=domain_graph`、`target=travel`。域图入口合法地将旧 `route_decision` 置空并切换 `route_mode=travel`；其余旧字段仍保留。

### C. 跨域切换

5 组客服↔旅游、客服↔数据、旅游↔选品、数据↔旅游切换均正确落到第二轮目标域和 capability。上下文摘要同时保留 `order_id` 与 `destination`，没有把旧域上下文当成全局锁。

### D. 模糊与 fallback

- `第二个呢？` 在 active travel 下命中 `continuation_hit` 并回到 travel。
- `价格是多少？` 无延续信号，不被误吸附到 travel。
- `太赶了，先统计本月订单金额` 返回 `domain_switch_allowed`，允许新域路由。
- 显式订单 `MO-3C052B3A` 被规范化为 `查询订单 MO-3C052B3A`。
- adapter 异常时保留旧 state，并写入 `router_fallback_reason=decision_adapter:*`、`legacy_used=true`。
- legacy direct `sql.query` 快照可序列化，旧 `route_decision`、`route_mode`、`query_understanding` 保持不变。

## 5. Router trace 示例

每轮结果均验证以下旁路观察字段存在且可 JSON 序列化：

```json
{
  "domain_decision": {
    "domain": "customer_service",
    "subflow": null,
    "confidence": 0.96,
    "source": "rule",
    "reasoning": "stop_c_matrix"
  },
  "capability_decision": {
    "domain": "customer_service",
    "capability": "customer_service",
    "candidates": [],
    "confidence": 0.93,
    "source": "hierarchical"
  },
  "execution_decision": {
    "mode": "direct",
    "target": "customer_service",
    "confidence": 0.93
  },
  "router_fallback_reason": "",
  "legacy_used": false
}
```

旧字段仍由原入口更新规则决定；测试明确区分“合法域图入口更新”与“旁路快照覆盖”。

## 6. 指标

```ini
DOMAIN_CONTINUITY=100% (10/10, A+B)
CAPABILITY_CONTINUITY=100% (5/5, A)
CROSS_DOMAIN_SWITCH_CORRECTNESS=100% (5/5, C)
FALLBACK_AND_AMBIGUITY_CONTRACT=100% (6/6, D)
LEGACY_FALLBACK_AVAILABLE=true
CHECKPOINT_COMPATIBLE=true
FRONTEND_EVENTS_UNCHANGED=true
```

其中 B 的旅游域没有强行虚构 fine capability，按域图 `target=travel` 单独统计；因此 capability 指标只在存在 capability 的 A 组上计算。

## 7. 发现的问题与修复

本次未发现生产 Router 连续性缺陷。首轮测试发现的是测试断言把合法的旅游 prefilter 更新（`route_decision=None`、`route_mode=travel`）误当成覆盖旧字段，已修正断言边界；未修改生产代码。

本次变更仅包含：

1. STOP B 冻结记录；
2. 21 个离线连续性/跨域/fallback 用例；
3. 本验收报告。

## 8. 验证命令

```text
D:/Python/python.exe -m pytest tests/router/test_cross_domain_continuity.py \
  tests/orchestration/router \
  tests/orchestration/context/test_continuation_routing.py \
  tests/orchestration/graph/test_router_prefilter_order.py \
  tests/orchestration/graph/test_router_understanding.py -q --no-cov
```

执行目录：`backend/`。结果：`159 passed`（其中 STOP C 专用矩阵 `21 passed`）。

该命令同时覆盖 STOP B Router adapter、domain/hierarchical/shadow、Continuation、prefilter 与 understanding 回归；未修改生产 Router 代码。

## 9. 最终 verdict

```ini
STOP_C_PASS=true
```
