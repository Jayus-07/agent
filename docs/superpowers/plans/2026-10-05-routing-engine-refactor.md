# 路由引擎收口实施计划

> **For agentic workers:** 本计划由当前会话按测试驱动方式逐项执行。

**目标：** 将主图路由收口为唯一的 `RoutingEngine` 生产路径，删除 legacy/hierarchical/shadow 的长期分叉和 `route_legacy()` 逃生口。

**架构：** 保留现有入口域门禁与 prefilter 顺序。主路由统一调用 `DomainRouter`、`CapabilityRouter`、`ExecutionModeResolver`，并由 `RoutingEngine` 负责缓存、fallback 和决策元数据。旧 `Router` 只作为兼容 facade，内部不再执行旧三层架构。

**技术栈：** Python、Pydantic、LangGraph、Prometheus、pgvector、pytest。

**设计文档：** `docs/superpowers/specs/2026-10-05-routing-engine-design.md`

## 全局约束

- 不改变客服、旅游、选品、预订、商务 prefilter 优先级
- 不改变 `RouteDecision` 下游字段语义
- 不在请求热路径同步重建向量索引
- 新增行为必须先写失败测试并确认失败原因
- 后端局部测试必须使用 `--no-cov`

## Review Focus

- 域图 prefilter 未放行时不得调用旧 Router
- 粗分类 embedding 故障必须进入结构化 fallback，不得静默伪装成普通 unknown
- 向量索引模型不匹配不得触发请求内重建
- 缓存键必须稳定，且不把每轮变化的会话摘要混入 legacy 键
- workflow、复合意图、general_chat、clarify 的执行方式不能回归

### Task 1: 建立统一 RoutingEngine

**Files:**
- Create: `backend/orchestration/router/engine.py`
- Modify: `backend/orchestration/router/__init__.py`
- Test: `backend/tests/orchestration/router/test_routing_engine.py`

- [x] 写测试：workflow 和复合意图走统一引擎，输出仍为 `RouteDecision`
- [x] 写测试：域分类、能力解析、执行方式 resolver 按固定顺序调用
- [x] 运行新增测试，确认在引擎不存在时失败
- [x] 实现最小 `RoutingEngine.route()` 和模块级单例
- [x] 运行新增测试并通过

### Task 2: 将主图切换到唯一引擎

**Files:**
- Modify: `backend/orchestration/graph/router_node.py`
- Modify: `backend/orchestration/graph/routing/hierarchical.py`
- Modify: `backend/orchestration/router/router.py`
- Test: `backend/tests/orchestration/router/test_routing_engine.py`
- Test: `backend/tests/orchestration/router/test_routing_shadow.py`

- [x] 写测试：router_node 主路由调用新引擎，不读取架构开关
- [x] 写测试：域图 prefilter 未放行时走统一 fallback，不调用旧路由方法
- [x] 运行测试确认旧调用仍存在导致失败
- [x] 替换主图调用，移除 hierarchical 到旧路由的回退
- [x] 将 `Router` 改为兼容 facade，内部委托 `RoutingEngine`
- [x] 运行主图路由守护测试

### Task 3: 收口向量依赖与故障语义

**Files:**
- Modify: `backend/orchestration/router/hierarchical.py`
- Modify: `backend/orchestration/router/vector_router.py`
- Modify: `backend/observability/metrics.py`
- Test: `backend/tests/orchestration/router/test_routing_engine.py`

- [x] 写测试：向量索引不匹配时不调用旧 Router，不触发 `_rebuild_index`
- [x] 写测试：向量故障带固定 fallback reason 并进入 LLM 兜底
- [x] 运行测试确认现有实现会绕旧 Router或同步重建
- [x] 让 hierarchical 直接持有向量 provider，不通过 `get_router()` 反向取向量
- [x] 区分 `vector_index_mismatch`、`vector_unavailable`、`llm_failure`
- [x] 增加低基数路由 fallback 指标
- [x] 运行路由与向量相关测试

### Task 4: 收口缓存和决策元数据

**Files:**
- Modify: `backend/orchestration/router/engine.py`
- Modify: `backend/orchestration/router/router.py`
- Modify: `backend/observability/metrics.py`
- Test: `backend/tests/orchestration/router/test_routing_engine.py`
- Test: `backend/tests/orchestration/test_orchestration_debt_fixes.py`

- [x] 写测试：动态会话字段不改变缓存键，身份作用域仍隔离
- [x] 写测试：策略/manifest/model 指纹变化会隔离旧缓存
- [x] 运行测试确认旧键把完整 context 全量序列化
- [x] 实现稳定缓存键和固定版本指纹
- [x] 记录 cache hit/miss、最终层级、fallback reason 和耗时
- [x] 运行缓存与路由测试

### Task 5: 删除旧生产分支并完成验证

**Files:**
- Modify: `backend/config/llm.py`
- Modify: `backend/config/__init__.py`
- Modify: `backend/orchestration/router/router.py`
- Modify: `backend/orchestration/router/hierarchical.py`
- Modify: `backend/tests/orchestration/router/test_routing_shadow.py`
- Modify: `backend/evaluation/run_router_eval.py`

- [x] 删除主流程对 `ROUTING_ARCHITECTURE` 和 `ROUTING_SHADOW_MODE` 的依赖
- [x] 将旧 shadow 测试改为新引擎契约测试
- [x] 移除 `route_legacy()` 和无调用方的旧三层实现
- [ ] 运行路由测试、主图 prefilter 守护测试和路由评测冒烟（评测冒烟需完整模型/索引环境，待验收会话执行）
- [x] 运行 `py_compile` 并检查最终 diff 范围
- [ ] 提交独立路由重构分支，供验收会话按提交验证
