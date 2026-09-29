# Architecture Simplification — STOP F 实施结案：Node Runtime Contract Closure

日期：2026-09-29
基线：`main@1136481`（STOP F 准备审计 `STOP_F_Preparation_Audit.md` 同仓发布）
性质：实施结案报告。设计蓝图以审计 §4/§6 收窄范围为准，本报告只记录落地事实与验证证据。

结论先行：

```text
NODE_RUNTIME_CONTRACT_PASS=true
TRACE_CONTRACT_UNCHANGED=true
EXPERT_CONTRACT_PASS=true
STOP_F_PASS=true
NODE_RUNTIME_REWRITE=false          # 只提取六段公共生命周期，非 Runtime 重写
TOOL_RUNTIME_TOUCHED=false
TRACE_MIDDLEWARE_TOUCHED=false
ADAPTER_TOUCHED=false               # 域图适配器 / builder / router / registry 零 diff
```

---

## 1. 修改范围（全部改动，无越界）

### 1.1 新增 `backend/core/node_runtime/`（6 文件，与 core/tool_runtime 平级同先例）

| 文件 | 内容 | 冻结约束 |
|---|---|---|
| `models.py` | `NodeResult`（frozen dataclass）+ `NodeStatus` | **仅 status/error/duration_ms/data 四字段**（审计 §6 风险5 硬约束，测试锁定字段清单）；fn 自报 status 原样透传 |
| `context.py` | `ExecutionContext`（frozen dataclass） | 仅 node_name/domain/deadline/tags 四字段，禁含业务状态 |
| `error_policy.py` | `ErrorPolicy`：RAISE_THROUGH / SWALLOW_TO_STATUS / FALLBACK；`TimeoutStrategy`：NONE / THREAD_ISOLATED | THREAD_ISOLATED 为**唯一**超时实现，注释携带 P2.3/B4 两案号 |
| `runner.py` | `NodeRunner.run()` 六段生命周期：log_start → timer → invoke（可选 TimeoutStrategy）→ exception policy → wrap → log_done | runner 不吞不吐（策略由调用方定）；钩子异常不兜底；THREAD_ISOLATED 无正 deadline 快速失败 |
| `hooks.py` | `ObservabilityHooks`（noop 默认）/ `CsExpertHooks`（metrics-only + 日志）/ `TravelExpertHooks`（span 软失败 + 日志） | CS 无 span（补 span = Deferred 独立评审）；Travel span 命名/形态原样迁移；metrics 导入保持在方法体内（@patch 可注入） |
| `__init__.py` | 公共 API 导出（9 个名字） | — |

### 1.2 迁移改写（仅两个文件，公开契约逐字节冻结）

- `backend/customer_service/experts/base.py`：`run_expert_safely` 签名/`ExpertResult` 字段/`ExpertStatus` 枚举/`ExpertType`/日志前缀/`record_cs_expert_result` 打点全部不变；内部改 `NodeRunner` + `SWALLOW_TO_STATUS` + `CsExpertHooks`，timeout_s>0 时 `THREAD_ISOLATED`。成功包装（`setdefault expert/status` + `duration_ms`）刻意留在 runner 之外，保持与旧实现同构（包装阶段异常外抛行为一致）。
- `backend/travel/experts/base.py`：`run_expert_safely` 签名/`TravelExpertResult` 字段/`TravelExpertStatus` 枚举不变；内部改 `NodeRunner` + `TravelExpertHooks`（span 生命周期原样迁移，`travel_expert_{name}` 命名与软失败语义不变）。包装放在 fn 侧（`_invoke`），保持失败包装不穿透的旧同构行为。

**调用方零改动**：CS 12 处调用点（5 专家 + `__init__.py` re-export）、Travel 8 处（5 专家 + re-export + 3 处测试直引）签名兼容，无一行修改。

### 1.3 新增测试 `backend/tests/node_runtime/`（3 文件 + `__init__.py`，40 用例）

| 文件 | 锁什么 |
|---|---|
| `test_contract.py` | NodeResult/ExecutionContext 字段清单冻结（防业务字段渗入）+ frozen；三 ErrorPolicy 语义（RAISE_THROUGH 不触发 on_error / FALLBACK 留痕但返兜底值）；状态透传（含 fn 自报非 success）；noop hooks 默认；钩子异常穿透；THREAD_ISOLATED 无 deadline fail-fast + 超时路径 |
| `test_parity.py` | **旧路径 vs 新路径同函数对照**（oracle = 迁移前手写实现快照，逻辑与 main@1136481 逐字节一致）：status/error/包装字段逐键相等、duration_ms 非负整数同量级、metrics 打点序列 @patch 对照一致、travel span（span_id/name/type/kind/status/metrics）成功与失败两态一致、无 trace 时软失败一致 |
| `test_cs_thread_isolation.py` | P2.3/B4 回归门：fn 跑在专用命名线程（cs-expert-*）非调用线程；contextvars 拷贝进线程可见；线程内变更不回流调用方；timeout_s=None 走调用线程（NONE 语义）；超时→恢复（不残留排队）；窗口内异常判 failed；timeout_s=0/负值降级为不限时 |

## 2. 未迁移组件（审计 §5 禁改红线，零 diff 实证）

`git diff HEAD` 范围核验，以下全部未触碰：

- **LangGraph**：node id / checkpoint（三处 `_build_checkpointer` + `travel:` 前缀）/ state schema / `builder.py` 接线——零 diff。
- **Router**：route_mode / prefilter 顺序 / registry——零 diff（守护用例 `test_router_prefilter_order.py` 在 graph 块 213 passed 内）。
- **Domain**：`DomainGraph.name` / subflow metadata / STOP E 语义边界——零 diff（`test_domain_registry.py` + `test_domain_semantic_consistency.py` 117 passed 内）。
- **Tool**：`core/tool_runtime` 九件套 / `tools/map/_base.py` 输出契约——零 diff（契约四件套 45 passed）。
- **Trace**：`TraceMiddleware` builder 接线 / span 名称 / trace 结构 / metrics 口径——零 diff；CS 专家保持无 span，Travel 专家 span 形态经 parity 测试逐字段对照一致。
- **明确不迁移**（同审计 §4）：域图适配器 5 个、validator/repair、selection stages、supervisor 调度语义、tool_runtime。

## 3. 验证记录（全部实跑，解释器 .venv Python 3.10.2，PGPORT=5433，`--no-cov`）

| 批次 | 结果 |
|---|---|
| 新增 `tests/node_runtime`（契约+parity+线程隔离） | **40 passed** |
| `tests/customer_service` 全量 | **1012 passed**（9 分 31 秒） |
| travel 块1（weather_expert/validator/repair/slot_filler/quality×2/scenarios） | **139 passed** |
| travel 块2（graph/checkpointer/契约×7/生命周期） | **202 passed, 1 failed**（见下） |
| travel 块3（providers/provider_layer/qweather_backup/persistence） | **87 passed, 1 failed**（见下） |
| orchestration 顶层 10 文件 | **117 passed** |
| orchestration router+context+workflow | **370 passed**（28 分 10 秒） |
| orchestration/graph（22 文件，含 router_prefilter_order 守护） | **213 passed** |
| tests/evaluation | **163 passed, 112 skipped** |
| 契约四件套（registry/layer/adr0001/base_output） | **45 passed** |

**两个失败均经基线归因为存量问题，与 STOP F 无关**（归因方法：暂存新版 → `git checkout` 还原基线 → 同用例重跑）：

1. `tests/travel/test_travel_graph.py::TestEndToEnd::test_plan_is_deterministic`——基线同样失败。宿主机环境依赖：risk 专家走 RAG 知识库检索触发「embedding API Key 未配置」降级重试（14 秒），两次图执行在降级路径上产生 `days` 内容差异。属既有宿主机病灶（记忆在案：travel 区宿主机必挂须分块）。
2. `tests/travel/test_provider_layer.py::TestHardening::test_t25_required_provider_semantics_frozen`——T25 冻结集 `{"tencent_lbs","weather","ticket"}` 未包含 `qweather_backup`。该行来自 f4f9628（和风备用源接入，同日早些时候）改 `providers/travel/live/health.py` 而未同步 T25 断言；本工作区该目录 diff=0。**属 f4f9628 的测试同步遗漏，待该线负责人补 T25 冻结集**（新增 qweather_backup 即可，语义不变）。

## 4. Runtime 兼容证明

1. **公开契约冻结**：两个 `run_expert_safely` 的签名（含 CS `timeout_s` 可选参数）、返回 TypedDict 字段、status 枚举值、日志前缀（`[CS Expert]` / `[Travel Expert]`）、metrics 名（`record_cs_expert_result`）逐字节保留；CS 15 处测试引用 + Travel 3 处直引零改动全绿。
2. **Parity**：新旧路径在成功/异常/超时/fn 自报状态四场景下结果逐键一致（`test_parity.py`，oracle 为迁移前实现快照）。
3. **生产事故语义锁**：P2.3（per-call 独立池防饿死误判）与 B4（contextvars 拷贝）以 `test_cs_thread_isolation.py` 六用例锁定为 THREAD_ISOLATED 唯一实现的行为回归门。
4. **Trace 形态不变**：Travel span 的 span_id 前缀/name/type/kind/status/metrics 键在成功与失败两态与旧实现逐字段一致；CS 保持 metrics-only 无 span；TraceMiddleware 接线零 diff。
5. **一致性门**：registry/layer/adr0001/base_output 四个契约测试 45 passed——注册体系与层约束未受影响。

## 5. 设计边界再确认

- `core/node_runtime` 不是第二套 Tool 治理：retry/breaker/bulkhead/deadline 属 `core/tool_runtime`，专家层唯一执行关切是 timeout，由 `TimeoutStrategy` 承载。
- 统一的是**机制**（六段生命周期 + 异常策略 + 钩子接口），不是遥测形态（CS metrics-only vs Travel span）、不是结果类型（三类域 TypedDict 各自保留）、不是异常策略选择（穿透/吞掉/兜底是 per-调用方 policy）。
- 异常策略三选一已由 runner 全量支持（RAISE_THROUGH 当前无调用方，为主图/未来节点预留，语义经测试锁定）。
- 最终形态对齐平台叙事：Agent Platform 下的 **Expert Runtime 层补齐**（Router/Domain/Capability/Expert/Tool/Workflow Runtime + Shared Platform），不是万能 Agent Runtime。

## 6. 遗留与后续

- **T25 冻结集同步**（f4f9628 遗留，非本 STOP 范围）：`test_provider_layer.py:501` 加入 `qweather_backup`。
- **CS 专家补 span**：Deferred（审计 §6 风险3），须独立评审，本 STOP 明确不做。
- **TraceMiddleware 内部是否改用 NodeRunner**：独立后续，前提是 span 形态逐字节一致。
- 下一步按路线：STOP G（Tool Contract）→ STOP H（最终架构文档）。

最后验证：2026-09-29（本报告第 3 节全部命令实跑）。
