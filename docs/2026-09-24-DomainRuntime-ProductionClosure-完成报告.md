# Multi-Agent Domain Runtime Production Closure — 最终完成报告

> 日期：2026-09-24 ｜ 执行：STOP A→F 串行验收制，前一 STOP 不 PASS 禁止继续
> 提交链：`9d1c9e5`(A-fix) → `6628fa8`(A-docs) → `6cc3f82`(B) → `093d173`(C) → `cfc8389`(D) → `912df8e`(E) → 本报告(F)

---

## 1. Verdict

```text
DOMAIN_RUNTIME_PRODUCTION_CLOSURE_PASS = true
DOMAIN_RUNTIME_CORE_FROZEN             = true
```

六个 STOP 全部 PASS；全程唯一 P0（选品 HTTP 路由无身份门禁）已在 STOP A 内闭环；无任何冻结层（Context Budget / Phase2 Task Runtime / Model Governance / Memory / Travel Infra）被重构。

## 2. Commits

| STOP | Commit | 内容 |
|---|---|---|
| A | 9d1c9e5 + 6628fa8 | P0-1 选品路由补 resolve_operator_role 门禁（5 文件，14 测试）+ 全链路只读审计报告 |
| B | 6cc3f82 | cs_pending_action 补登记（唯一生产改动）+ tests/domain_runtime 五文件 37 用例 + 八目录回归 2681 passed |
| C | 093d173 | 真实链路十轮跨域矩阵 10/10 + 逐轮 trace 拓扑路由断言 |
| D | cfc8389 | 冻结层 R1-R5 重跑 5/5 + 域信封/恢复身份/trace 关联/owner 校验/工单幂等 4/4 |
| E | 912df8e | 故障演练 E3/E5/E7/E8/E11/E12/E13/E14/E15 全 PASS（隔离实例法，共享栈零改动） |
| F | 本报告 | Production Baseline & Freeze（冻结点回归 100 passed） |

## 3. Architecture（最终 Runtime 调用图）

```
POST /api/chat/stream (APISIX gateway-auth → 身份头注入)
→ FastAPI chat_stream（resolve_identity / meta 帧 / producer 线程）
→ GraphRunner.iter_events
   Input Guard → (CS 业务门禁·锁域) → trace.start → memory.start_session
   → make_initial_state → FollowUpResolver → routing_context 组装
   → RequestContext(checkpoint_safe) → worker 线程 graph.stream
      router_node（域锁 → TravelPending → Continuation → greeting → redirect_main
                   → CS prefilter ≥2 组 → travel 正则 → selection 正则 → CS 兜底
                   → L1 clarify → 三层 Router/hierarchical）
      → route_selector 条件边
         ├─ cs_graph_node / travel_graph_node / selection_funnel_graph_node（域图→END）
         ├─ direct：tool_selector → skill_executor → reporter
         ├─ workflow：workflow_executor → reporter
         ├─ plan：planner → critique → supervisor(Send) → skills → reporter
         └─ general_chat / clarify
   → 事件汇流 merged_q → end_turn（CS 轮豁免）/ CS 独立落库
→ SSE：meta → status/log/delta/… → done|error
```

## 4. Domain Matrix

见 `docs/2026-09-24-DomainRuntime-ProductionBaseline-Freeze.md` §F1（6 域 × Router Key × Graph × State × Async × Side Effect）。

## 5. Shared / Private State

共享（F2 必填清单）vs 域私有（域 TypedDict + 单一 `*_context` 出口）。`sql_context/selection_context` 平铺命名禁止（测试锁定）；`cs_pending_action` 本轮补登记入 schema。泄漏防线：域上下文读取点封闭（CS 实体只进 CS 入口；travel pending 受 active_domain 门控；SQL/General 零域上下文消费通道——FakeGraph 捕获适配器真实入参证明）。

## 6. Routing

- **continuity**：延续信号词（≤40 字）+ 无外域强信号 → 回 active_domain；travel 结构化 pending 短路（resume/cancel/avoid/new_run 四通道）；CS 指代解析在判域前。
- **switching**：外域强信号优先——CS pending 不粘滞 travel 强信号（B4+运行时 T6/T8 双向实证）。
- **fallback**：LLM 失败/异常 → plan+rag.search（conf 0.3）→ 拒答兜底；绝不随机进高权限域。
- **unknown**：hierarchical 显式 unknown→clarify；legacy 落 plan 拒答兜底。
- 实测：CS 全局入口为 precision-tuned（≥2 规则组）；单组信号查询走主路由拒答兜底，客服抽屉产品面由 domain_hint 锁域覆盖。

## 7. Identity

tenant/user/session/roles 经网关验签注入 → RequestContext(checkpoint_safe) → state 平铺键 → bind_from_state 节点重绑 → Tool ContextVar →（异步）tasks 行权威 + worker 重建 + 执行时现查 auth.users。trace_id 不入 state（随 ctx/日志）。已知边界：CS 子图不消费 roles（归属+确认状态机授权）；travel 子图无 tenant（只读）；checkpoint/会话表无 tenant 列（P1-4）。

## 8. Permission

SQL：sql.read 门 + 六层校验 + agent_readonly + 行级 scope（E3/C 实证 promotions 拒绝）｜CS：归属校验 + 确认 CAS + require_cs_supervisor｜RAG：KB ABAC + 文档级 permission_scope（None 即拒）｜写工具：ensure_approved + 副作用预算门｜异步任务：执行时授权 fail-closed + authorization_denied 指标｜运营面：resolve_operator_role（STOP A P0-1 已收编选品两路由）。

## 9. Async Integration

域图全部同步；异步只经冻结 Task Runtime：Celery 消息只带 task_id → tasks 行身份五元组权威（D1 8/8）→ worker 绑定 + 执行时授权 → lease/fencing（D6 双实证）→ pause/resume 换发 execution 不重路由（D3）→ recovery 域身份保持（D2）→ trace session_id=thread_id 跨执行关联（D7）→ 结果经 owner 校验回传（D8，404 隔离）。

## 10. Side Effects

18 项台账（STOP A §10）全部带既有幂等保护：幂等账本 / 确认 CAS / fenced 写 / outbox / file_hash / decision_version / run CAS。未新造 ledger。

## 11. Checkpoint / Memory

Checkpoint 四 thread 构成（travel: 前缀 namespace 隔离，B9 测试锁定）；CS 业务态 DB 权威 + state_loader 每轮重载。Memory：CS 轮豁免 chat_messages/L3（C9 实证）；L3 管线证据门+冲突裁决；PinnedContext 请求级（确认实体保真）。已知风险 P1-9（travel 补槽短答可被 L3 永久化）归 Memory STOP C（已由并行会话落地 538145a，后续复核）。

## 12. SSE

事件全集：meta{node_labels,request_id} / status / log / delta / thinking / clarification / todo / usage / context / file / done{elapsed,sources,usage,trace_id,pending_action,context_usage} / error（统一错误壳）/ ping。done 每轮恰一次；断连 GeneratorExit→stop_event+trace client_disconnect 收尾；背压→截断帧。

## 13. E2E Matrix

见 STOP E 报告 §E16（15 行矩阵全 PASS，含证据指针）+ STOP C 十轮矩阵。

## 14. Failure Drill

router 依赖故障（E5）／Redis（E7 memory fallback 出单）／PG（E8 启动 fail-closed，无半成功）／worker death（R3 takeover）／client disconnect（E12）／duplicate（E13 + R 矩阵 SUCCESS_NOOP）／权限拒绝（R2）——全部真实环境实机。披露：LLM provider 级故障未实机注入（治理后凭据在 DB）；E9 用 lease sweep（真实 SIGKILL 归 Phase2 记录）；E11 未实机触发 L5（测试族覆盖）。

## 15. Observability

trace：trace_id 五层贯穿 + task trace session_id=thread_id（容器 SQLite 权威存储）；metrics：路由/域/LLM/任务/预算/错误六族齐全，label 禁令多处明文（无用户全文）；logs：路由层记录 query（策略内，P2-11）；audit：CS 审计表（幂等落库）/SQL hash 审计/审批单状态机/幂等决策 tags。

## 16. Regression

- STOP B：八目录 2681 passed / 3 failed（2 BASELINE_RED + 1 顺序 flake，clean worktree 对照在案）
- 冻结点：契约四门 + domain_runtime 37 + 选品门禁/RBAC = **100 passed**
- 实机：STOP C 10/10、STOP D 9/9、STOP E 9/9（含 E3 租户隔离）

## 17. Remaining Risks（如实）

| 级别 | 风险 | 归属 |
|---|---|---|
| P1-4 | chat_sessions/chat_messages/checkpoints 无 tenant 维度（单租户现实） | 多租户化前必须补 |
| P1-5 | session_id body 可控为 CS/travel checkpoint 根（执行态面） | 多租户化前加 user 维度 |
| P1-9 | travel 补槽短答可被 L3 永久化（memory 无 transient 过滤） | Memory STOP C 复核 |
| P1-14 | WorkflowExecutor 无身份装配（选品后台执行体） | 后续工作包 |
| P2-10 | ai.trace_records PG 镜像 0 行（trace 仅容器 SQLite） | 观测导出缺口 |
| P2-11 | query 文本进路由 INFO 日志（既有策略） | 生产建议采样降级 |
| 部署 | app 容器 bake 镜像滞后主干（本次实证容器=工作区同码，但历史上有滞后坑） | 改动后必须 rebuild |

---

## 最终验收问题（§13）——全部可即时回答

答案矩阵固化于 STOP A §13（16 问逐条，含出处 file:line / 实机证据指针），冻结后任何一问不再需要「靠猜、翻日志、按名字推断」：

> 为什么路由到这个域？（trace 拓扑 + routing_domain_total）｜上轮影响？（三条受控粘滞通道）｜私有状态在哪/何时失效？（F3 五层边界）｜切域泄漏？（读取点封闭+37 用例）｜tenant/user 贯穿？（§7 身份矩阵）｜worker 里身份？（tasks 行权威+执行时授权）｜权限哪层再校验？（§8）｜恢复知道原域？（graph_name 固定+thread 不变）｜旧 execution 能写？（fencing 拒绝）｜重复请求几次？（幂等台账收敛）｜Router 挂了？（plan+拒答兜底）｜模型挂了？（Governance fallback）｜Redis/PG 挂了？（memory fallback / 启动 fail-closed）｜SSE 断开？（trace 收尾+无孤儿）｜Trace 闭环？（五层贯穿，P2-10 限定库侧查询）。

```text
DOMAIN_RUNTIME_PRODUCTION_CLOSURE_PASS = true
DOMAIN_RUNTIME_CORE_FROZEN             = true

从此 Domain Runtime 进入稳定基础设施阶段：
后续业务开发 = 新增 Domain / Expert / Tool / 业务状态 / 数据源，
而非反复修改 Router / GraphRunner / 身份链 / Async Runtime / Memory。
```
