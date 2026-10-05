# 路由引擎收口设计

## 目标

把主图路由从 `legacy + hierarchical + shadow` 三套长期并存的实现，收口为一条由域判断、能力解析和执行方式决策组成的生产链路。迁移完成后，旧路径只保留兼容期所需的最小适配，不再作为可选生产架构长期维护。

## 当前问题

- `backend/orchestration/router/router.py` 同时承担 legacy 三层路由、hierarchical 分层路由、shadow 对比、缓存和异常降级
- hierarchical 粗分类失败时回退 `route_legacy()`，导致新旧架构互相调用
- 域图 prefilter 未放行时再次进入 `route_legacy()`，存在绕过统一决策协议的逃生口
- `VectorRouter` 通过旧 Router 获取向量能力，形成反向依赖
- 任意路由异常都可能统一落到 `plan`，无法区分不确定性和基础设施故障

## 目标架构

```text
router_node 入口门禁与域 prefilter
        ↓
RoutingEngine
  ├─ DomainRouter：只判断业务域
  ├─ CapabilityRouter：只解析域内候选
  └─ ExecutionModeResolver：只决定执行方式
        ↓
统一 RouteDecision + routing_meta
        ↓
route_selector → domain graph / direct / workflow / plan / clarify / general_chat
```

### 责任边界

- `router_node`：保留客服锁域、续跑、域 prefilter 和入口追问的既有业务顺序；不再直接选择 legacy 或 hierarchical 架构
- `DomainRouter`：消费 prefilter 结果或粗分类器结果，只输出 `DomainDecision`
- `CapabilityRouter`：只在已确定的域内选择候选，不执行 Tool、Skill 或 Workflow
- `ExecutionModeResolver`：根据域决策、能力候选和显式 override 输出执行方式
- `RoutingEngine`：负责上述三者的组装、缓存、结构化降级和统一观测
- `RouteDecision`：继续作为下游兼容协议，`routing_meta` 保存结构化证据，不新增执行职责

## 故障语义

- 低置信度属于业务不确定性，输出 `clarify`，不强行猜测
- 向量索引不兼容、向量服务不可用属于基础设施故障，记录固定 `fallback_reason`，进入明确的 LLM 解析兜底；不触发同步重建，不调用旧 Router
- LLM 解析失败才使用受控的 `plan` 安全兜底，并保留故障原因
- 域 prefilter 未放行时使用统一引擎的 fallback，不允许 `route_legacy()` 绕过主协议

## 缓存与版本

- 缓存键使用稳定 JSON 序列化
- 会话动态字段不进入键；只纳入实际消费的身份作用域
- 键包含架构策略版本、manifest 指纹、模型标识和上下文作用域
- 用户输入、用户 ID、租户 ID 不进入 Prometheus label

## 迁移策略

1. 先让新引擎覆盖主图未命中 prefilter 的主路由路径
2. 更新域图异常和低置信分派，移除对 `route_legacy()` 的依赖
3. 删除 `ROUTING_ARCHITECTURE`、`ROUTING_SHADOW_MODE` 对主流程的分支依赖
4. 保留兼容 facade 供少量外部调用迁移，测试和内部调用全部切换到新引擎
5. 通过路由单测、主图路由守护测试和评测脚本后，删除 legacy 实现和 shadow 代码

## 非目标

- 本次不训练新的分类模型
- 本次不改变客服、旅游、选品、预订、商务 prefilter 的业务优先级
- 本次不改变下游 Skill、Tool、Workflow 的执行协议
- 本次不把路由评测扩展成完整发布平台；先复用现有评测入口建立可回归基线

## 完成标准

- 主图生产路径不再读取 `ROUTING_ARCHITECTURE` 或 `ROUTING_SHADOW_MODE` 决定架构
- 主图生产路径不再调用 `route_legacy()`
- 向量层故障不会同步重建索引，也不会静默吞掉故障原因
- 低置信、不兼容、LLM 失败三类出口可由结构化字段区分
- 路由相关测试全部通过，新增测试先失败后实现
