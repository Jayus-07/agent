# ADR-0014: 统一大模型网关模块化架构（Modular LLM Gateway Architecture）

- **状态**：Accepted（已随 PR #573 落地实施）
- **日期**：2026-11-26
- **关联代码**：`backend/infra/llm/gateway/`、`backend/infra/llm/proxy.py`

---

## 一、 背景与动机

在 PR #573 之前，全平台所有云端大模型外呼、模型降级、Token 计量、预算门禁与流式处理全部挤压在单个单体文件 `backend/infra/llm/proxy.py` 中，总代码量高达 **2876 行**。

### 历史单体设计暴露的核心痛点
1. **职责过载严重违背单一职责原则（SRP）**：单个类中同时混杂着 HTTP 传输异常重试、断路器状态流转、分布式 Redis 限流、上下文预算滑动截断、SSE 分片解析与数据库账本落账；
2. **并发身份串扰风险**：多租户高并发异步协程环境下，请求级覆盖模型、用户身份与诊断回调的传递链路过长，容易在嵌套或异常回退时发生变量泄露；
3. **架构防腐与测试隔离性差**：任何对 Token 计费逻辑或流式分片解析的微小改动，都必须加载完整的外部供应商 SDK，单测难以对断路器或预算门禁进行轻量化测试。

---

## 二、 架构决策

平台决定彻底废除单体 `proxy.py` 的巨型实现，采用**责任链模式（Chain of Responsibility）结合外观模式（Facade）**将其解耦拆解为独立的现代化网关子包 `backend/infra/llm/gateway/`：

```
backend/infra/llm/gateway/
├── __init__.py           # 门面导出层：对外暴露 100% 兼容历史 proxy.py 的公开接口
├── base.py               # 动态探针与穿透辅助：透明代理穿透与运行时自省
├── bound_proxy.py        # BoundProxy 外观：bind_tools_for_model / bind_llm_for_role 参数绑定
├── budget_guard.py       # 五级预算与速率门禁：Redis 限流、上下文预算动态滑动截断
├── resilience.py         # 韧性容灾体系：瞬态错误识别、指数退避重试、断路器与降级响应
├── streaming.py          # 流式分片处理：SSE StreamSink 管理与 reasoning_content 分离
├── accounting.py         # 计量记账引擎：Token 累加、流式用量估算与数据库不可变账本落账
└── context.py            # 线程与协程上下文：基于 ContextVars 的身份隔离与调用诊断
```

### 关键架构决策点

#### D1. 外观模式（Facade）保持 100% 向后兼容
- `backend/infra/llm/proxy.py` 精简为一个轻量级的向前兼容门面，直接从 `backend.infra.llm.gateway` 导入全部公开函数与类；
- 所有历史调用方（如 LangGraph 节点、RAG 检索链、SQL 生成器、后台任务）**无需修改任何一行导入语句**，实现平滑无感升级。

#### D2. 职责链分流与生命周期解耦
- **调用前（Preflight）**：由 `budget_guard.py` 与 `context.py` 接管，执行分布式速率限制、日/月额度预检、模型输入单价分级门禁与上下文预算滑动截断；
- **调用中（Execution & Resilience）**：由 `resilience.py` 包装重试退避算法，精准识别 `_TRANSIENT_MARKERS` 瞬态错误，断路器自动熔断不健康 Provider；
- **流式中（Streaming）**：由 `streaming.py` 驱动 `StreamSink`，将思考型模型的思维链（`reasoning_content`）与正式业务回答（`text`）实时解耦；
- **调用后（Accounting & Settlement）**：由 `accounting.py` 负责 Token 累加与不可变账本落账，并联动数据库物理触发器 `trg_budget_resolution_audit_append_only`（迁移 146）确保账本只追加不可篡改。

#### D3. 上下文隔离机制（ContextVars）
- 摒弃脆弱的全局变量或随意传递的字典，网关使用 Python 标准库 `contextvars` 严格维护 `_thread_local_user_id`、`_last_tokens_var`、`_turn_usage_var`；
- 在异步并发调用中，每个协程的调用诊断与计费用量严格隔离，杜绝跨租户身份串扰。

---

## 三、 收益与验证

1. **代码可读性与维护性跃升**：`proxy.py` 减少了超 2700 行冗余代码，各个子模块平均代码量仅 200~500 行，职责界限极度清晰；
2. **测试覆盖率与架构守卫**：新增专属架构守卫测试 `backend/tests/infra/test_llm_proxy_modular_architecture.py`，并已挂载入 CI 流水线，永久守卫模块化依赖边界；
3. **系统韧性提升**：断路器与降级响应与业务代码解耦，在云端大模型发生 429 或 5xx 故障时，可秒级优雅降级，主应用业务流永不崩溃。
