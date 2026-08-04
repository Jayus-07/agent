# ADR-0002: 拆解 RAGChain god class

| 项 | 值 |
|----|----|
| **状态** | Partially Accepted (PR-1.1 完成；PR-1.2/1.3/1.4 待实施) |
| **日期** | 2026-08-04 |
| **作者** | wh |
| **影响范围** | `backend/rag/chain.py` (886 → ~300) · `backend/rag/evidence_gate/` · 整个 RAG 链路 |

---

## 背景

`backend/rag/chain.py` 在过去 30 天累积 **40 个 commit**（基于 `git log`），是项目**修改最频繁的文件**。其规模与复杂度已突破单一职责边界：

### 量化指标

| 指标 | 值 |
|------|----|
| 总行数 | **886 行** |
| 类方法数 | **23 个** |
| mutable 字段数 | **6 个**（`_last_intent` / `_risk_level` / `_last_query_analysis` / `_last_query` / `_last_meta` / `_self_correction_*`） |
| 直接 import 的子系统 | `tracer` / `evidence_gate` / `guardrails` / `multi_query` / `memory` / `llm` |
| 历史 commit 数 | **40**（2026-06 ~ 2026-08） |
| 最近一次 god class 信号 | 0a30203（inventory 接入 8 step DAG）→ 032aab7 修复 5 个冲突 |

### 症状（用户可见）

1. **补丁堆积循环**：每次新增能力（Evidence Gate / Self-Correction / Citation 校验 / Faithfulness）都直接在 `RAGChain` 加方法，40 次 commit 都围绕这个类
2. **变更半径失控**：单次修改（如改拒答文案）必须触碰 `RAGChain._verify` / `_handle_llm_reject` / `_build_decision_from_meta` / `_reject` 4 处
3. **测试脆弱**：测试 fixture 直接读写 `_last_intent` / `_self_correction_retry_count`（2d627d7 tracer 重构时 48 个测试红就是这个债）
4. **耦合违反 CLAUDE.md**：`Skill 不直接访问外部系统` 原则 — `RAGChain` 反向 `from backend.rag.tracer import trace_collector` 和 `from backend.orchestration.supervisor.alerts import make_alert`

### 根因分析

RAGChain 当前承担 5 类职责：

| 职责 | 涉及方法 | 行数占比 |
|------|---------|---------|
| 链构造（LangChain LCEL） | `_build_chains` | ~10% |
| 检索编排（MultiQuery / Hybrid / Rerank） | `_execute` | ~15% |
| Evidence Gate 决策 | `_run_evidence_gates` / `_build_decision_from_meta` | ~20% |
| Self-Correction 状态机 | `_can_self_correct` / `_try_self_correct` / `_rewrite_query` | ~20% |
| 拒答格式化 + Trace 收尾 | `_verify` / `_reject` / `_handle_llm_reject` / `_finish` | ~25% |
| 杂项（utility / 字段管理） | 其他 | ~10% |

---

## 决策

**将 RAGChain 重构为 orchestrator + 4 个独立策略对象**：

```
RAGChain (orchestrator, ~300 行)
├── EvidenceGateController   # PR-1.1 ✅ — 5 类 RejectReason 决策
├── SelfCorrectionStrategy   # PR-1.2 — 重试状态机 + query 改写
├── CitationFormatter        # PR-1.3 — think-tag 剥离 + 引用编号格式化
└── FaithfulnessScorer       # (后续)  — NLI 评分
```

### 核心原则

1. **策略对象自治状态**：每个对象持有自己的 mutable 字段（如 `_last_intent` / `_self_correction_retry_count`），不依赖 RAGChain 字段
2. **RAGChain 仅编排**：持有 4 个策略对象 + 链路构造 + 对外接口
3. **策略对象可独立单测**：测试不依赖 RAGChain 整套构造
4. **依赖方向单向**：`RAGChain` → 策略对象，不反向 import

### 不变的部分

- 公共 API `ask(question, session_id) -> str` 不变
- Trace span 名不变（前端不变）
- 拒答用户消息不变（5 类 RejectReason 文案冻结）

---

## 备选方案

### 备选 A：维持 god class + 内部按职责分文件

- 把 `_run_evidence_gates` 等方法移到 `chain_evidence.py` / `chain_correction.py`，RAGChain 通过 mixin 组合
- **否决理由**：mixin 状态共享仍隐式，且 Python mixin 不是惯用模式，团队学习成本高

### 备选 B：完全打平为模块函数

- 删 `RAGChain` 类，所有方法变成 `rag_query(question)` / `evidence_gate_run(...)` 等模块函数
- **否决理由**：破坏现有 import 路径（Planner / Supervisor / tests 都依赖 `RAGChain()` 实例），改动半径过大

### 备选 C：拆为 4 个策略对象（采用方案）

- **优点**：
  - 每个策略 100-200 行，可独立单测
  - 状态隔离（每个对象自己的字段）
  - 未来加新策略（如 FaithfulnessScorer）不触碰 RAGChain
- **缺点**：
  - 4 个 PR 的重构期较长（~1 周）
  - 现有测试 fixture 要更新（PR-0.1 已部分修好）

---

## 与 CLAUDE.md 8 层架构的关系

CLAUDE.md 定义的复杂请求调用链：

```
API → Router / Agent Runtime → Planner → Supervisor → Skill → Tool → MCP Client → MCP Server → External Resource
```

### 拆解前的架构偏差

RAGChain god class 实际上是 **「Agent Runtime + 部分 Tool」两层职责的混合体**：

| CLAUDE.md 层 | 当前实际承担方 | 偏差 |
|--------------|----------------|------|
| **Agent Runtime** | `RAGChain` 类 | ❌ 没有独立模块，混在 `rag/chain.py` |
| **Tool** | `_build_chains` / `multi_query.py` / `retrievers.py` | ⚠️ 散落多文件，无统一基类 |
| **MCP Client** | `mcp/manager.py` | ❌ 与 MCP Server 耦合 |

### 拆解后的架构对齐

PR-1.1 ~ PR-1.4 完成后，RAGChain 瘦身到 ~300 行的 orchestrator，**回归它本应承担的「Skill 内部编排」职责**，而不再越界当 Agent Runtime：

```
rag_skill 调用链（拆解后）：
RAGChain.ask()                                 # orchestrator（~300 行）
    ├── EvidenceGateController                  # Agent Runtime 决策职责
    ├── SelfCorrectionStrategy                 # Agent Runtime 策略职责
    ├── CitationFormatter                       # Tool 职责（格式化）
    └── chain.invoke(LangChain LCEL)            # Tool 职责（LCEL 链）
        ├── HistoryAwareRetriever
        ├── MultiQueryRetriever
        ├── ChunkLevelRetriever
        ├── AdaptiveRetriever
        └── RerankCompressor
```

**关键点**：本次拆解**不补 Agent Runtime 独立层**（CLAUDE.md 写但代码缺的层），而是**让 RAGChain 严格退回 Skill 内部**。Agent Runtime 独立化是后续 ADR 议题（见「后续工作」）。

### 跨层 import 治理

拆解前后 `RAGChain` 的 import 变化：

| 来源 | 拆解前 | 拆解后 |
|------|--------|--------|
| `backend.rag.tracer` | 直接 import | 策略对象持有 tracer 引用，不经 RAGChain |
| `backend.orchestration.supervisor.alerts` | 直接 import | 删除（CLAUDE.md 禁止 Skill 跨域访问） |
| `backend.rag.evidence_gate` | 直接 import | 通过 `EvidenceGateController` 间接调用 |
| `backend.rag.guardrails` | 直接 import | 移到 `_evaluate` 内部（已是这样） |

**修复 2 个架构违例**（来自重构调研）：
- Violation C: `rag/chain.py` 反向 deep-import `tracer` → 拆解后隔离到策略对象
- Violation D: `BaseSkill.execute` 跨域 import supervisor → 不在本次范围，下个 ADR 议题

---

## 设计细节

### PR-1.1：EvidenceGateController（已合并）✅

```python
# backend/rag/evidence_gate/controller.py
class EvidenceGateController:
    def __init__(self):
        self._last_intent = "summary_query"
        self._risk_level = "low"
        self._last_query_analysis = None

    def set_intent(self, intent) -> None: ...
    def set_risk_level(self, level) -> None: ...
    def set_query_analysis(self, analysis) -> None: ...
    def build_decision_from_meta(self, meta) -> GateDecision: ...
```

**抽出字段**：`_last_intent` / `_risk_level` / `_last_query_analysis`
**抽出方法**：`_build_decision_from_meta`（11 行 → 完整方法 + 错误兜底）
**配套改动**：`rag/evidence_gate.py` (438 行单文件) → `rag/evidence_gate/` 包（`__init__.py` 保留旧导出 + `controller.py` 新增）
**测试**：18 个新单测覆盖状态读写 + 5 种 RejectReason + 兼容性

### PR-1.2：SelfCorrectionStrategy（待实施）

```python
# backend/rag/evidence_gate/self_correction.py
class SelfCorrectionStrategy:
    def __init__(self, max_retries: int = 2):
        self._retry_count = 0
        self._pending_state = None

    def can_retry(self) -> bool: ...
    def record_attempt(self, original_decision, trace) -> None: ...
    def try_rewrite(self, question, reason) -> str: ...
    def reset(self) -> None: ...
```

**抽出字段**：`_self_correction_retry_count` / `_self_correction_pending`
**抽出方法**：`_can_self_correct` / `_try_self_correct` / `_rewrite_query`（3 个方法共 ~70 行）
**决策表**：low/medium/high risk 各自的 retry 上限

### PR-1.3：CitationFormatter（待实施）

```python
# backend/rag/chain/citation.py
class CitationFormatter:
    def format(self, answer: str, evidence: list) -> str:
        # 1. 剥离 think-tag
        # 2. 替换 [1][2] → [[来源1]]
        # 3. 附加 references 块
        ...
```

**抽出方法**：`_verify` 中的 `format_citations()` 部分（~30 行）
**单职责**：think-tag 剥离 + 引用编号替换 + 格式化输出

### PR-1.4：RAGChain 瘦身为 orchestrator（待实施）

```python
# backend/rag/chain.py (886 → ~300)
class RAGChain:
    def __init__(self, ...):
        self.gate = EvidenceGateController()        # PR-1.1
        self.corrector = SelfCorrectionStrategy()   # PR-1.2
        self.formatter = CitationFormatter()        # PR-1.3
        self._build_chains()                        # 不变
        # 删除 6 个 mutable 字段（已迁到策略对象）
        # 删除 12 个方法（已迁到策略对象）

    def ask(self, question, session_id):
        # 编排：trace → retrieve → gate → generate → correct → format → finish
        ...
```

**移除**：6 个 mutable 字段 + 12 个方法
**保留**：`_build_chains` / `ask` / `_start` / `_execute` / `_respond` / `_finish`（编排骨架）

---

## 影响

### 代码改动

| 文件 | 改动类型 | 行数变化 |
|------|---------|---------|
| `rag/chain.py` | god class → orchestrator | 886 → ~300 (-586) |
| `rag/evidence_gate.py` → `rag/evidence_gate/` | 单文件 → 包 | 438 → 0（移入 `__init__.py`）+ controller.py 100 |
| `rag/evidence_gate/self_correction.py` | 新建 | +150 |
| `rag/chain/citation.py` | 新建 | +80 |
| `backend/tests/` | 新增/更新测试 | +60 |

### 性能

- 4 个对象调用链增加 4 次属性查找（< 1μs，可忽略）
- 无网络/IO 调用增加
- 现有 trace 性能不受影响

### 兼容性

| 接口 | 影响 |
|------|------|
| `RAGChain().ask(q, sid)` | 不变（公共 API） |
| `RAGChain().chain` | 内部属性，可能外部测试用 → PR-1.4 同步更新 |
| Trace span 名 | 不变（前端的 SPAN_STAGE_MAP 继续工作） |
| 5 类 RejectReason 文案 | 不变（冻结） |

### 风险

| 风险 | 缓解 |
|------|------|
| 现有测试访问 `chain._last_intent` 等内部字段 | PR-0.1 已修复 90%，剩 10% 在 PR-1.2/1.3/1.4 期间持续修 |
| 4 个 PR 期间行为漂移 | 每 PR 跑全量 452 测试，0 红 merge |
| 策略对象共享同一 trace 上下文 | 用 `trace_collector` 模块级 API（已 thread-safe） |
| 新成员对 4 文件结构不熟 | ADR + 模块级 docstring + 1 个 architecture overview 图 |

---

## 验证标准

> 对齐 CLAUDE.md「Validation」章节：py_compile + pytest；端到端是必要不充分条件。

### 静态验证（每 PR 自动跑）

- [ ] `python -m py_compile backend/rag/chain.py backend/rag/evidence_gate/*.py backend/rag/chain/citation.py` 0 错误
- [ ] `mcp__code-review-graph__find_large_functions_tool --min_lines 80` 中 `rag/chain.py` 不在结果
- [ ] `grep -rn "from backend.orchestration.supervisor" backend/rag/` 只在 1 处（orchestration 域内合法）
- [ ] `grep -rn "from backend.orchestration" backend/rag/evidence_gate/` 0 处（CLAUDE.md 禁止 Skill 跨域）

### 单元测试（每 PR 自动跑）

- [x] PR-1.1: `pytest tests/test_evidence_gate_controller.py -v` 18/18 通过
- [ ] PR-1.2: `pytest tests/test_self_correction_strategy.py -v` ~15 个新单测
- [ ] PR-1.3: `pytest tests/test_citation_formatter.py -v` ~10 个新单测
- [ ] PR-1.4: `pytest tests/ -q` 整体 485 → 530+ 通过
- [ ] `from backend.rag.evidence_gate import GateDecision, RejectReason, EvidenceGateController` 兼容（不破外部 import）

### 集成测试（PR-1.4 完成时跑）

- [ ] `pytest tests/test_adr0001_dual_registry_merge.py -v` 全绿（验证 Skill 注册表仍工作）
- [ ] `pytest tests/test_rag_p1_self_correction.py -v` 全绿（验证 self-correction 状态机）
- [ ] `pytest tests/test_evidence_gate.py -v` 全绿（验证 Gate 决策）

### 端到端验证（4 个 demo 场景，PR-1.4 完成时手动跑）

- [ ] **RAG QA 场景**：`curl -X POST http://localhost:8000/chat -d '{"question":"Amazon FBA发货 SOP"}'` 返回 SSE 流，含 `[1][2]` 引用
- [ ] **Daily Report 场景**：跑 `python scripts/demo.py daily_report`，生成日报写到 `daily_reports` 表
- [ ] **Inventory Alert 场景**：跑 `python scripts/demo.py inventory_alert`，触发库存预警
- [ ] **Knowledge Index 场景**：上传 PDF，3 分钟内 8 阶段 trace 全部完成
- [ ] **Trace span 名核对**：前端 `/observability/traces/[id]` 页面能正确显示中文 span 名（不变）

### 性能验证（PR-1.4 完成后 1 周内）

- [ ] 4 个策略对象调用链额外延迟 < 1ms（属性查找，无 IO）
- [ ] 现有 `/chat` 端到端 P99 延迟无变化（基准：< 5s）
- [ ] 单元测试 + 集成测试 跑全量 < 3 分钟（基线 2 分 10 秒）

### 文档验证（PR-1.4 完成时）

- [ ] ADR-0002 状态从 `Partially Accepted` → `Accepted`
- [ ] `docs/architecture/structure.md` 更新 RAGChain 章节（标注「orchestrator + 4 策略对象」）
- [ ] `docs/architecture/rag-system.md` 链接到 ADR-0002
- [ ] `CHANGELOG.md` 记录拆解（4 个新模块）

### 回归保护（每 PR 都跑）

- [ ] 现有 7 个 Skill 全部可调用（sql/rag/report/email/web_search/web_crawl/data_export）
- [ ] 路由层 5 类 API 全部 200（chat/rag/observability/memory/inventory_alerts）
- [ ] Trace 持久化到 SQLite（`/metrics` 端点显示 trace 计数）

---

## 实施步骤

1. **PR-1.1** EvidenceGateController ✅（2026-08-04 已合并）
2. **PR-1.2** SelfCorrectionStrategy（预计 1 天）
3. **PR-1.3** CitationFormatter（预计 1 天）
4. **PR-1.4** RAGChain 瘦身 + 接线（预计 1 天）
5. 端到端验证 + 更新架构文档
6. 写 CHANGELOG

---

## 后续工作

- **FaithfulnessScorer** 抽为第 4 个策略对象（待 `_evaluate` 进一步成熟）
- **RAGChain `_build_chains` 进一步拆**：当前 85 行构造 LCEL 链，可拆 `ChainBuilder` 类
- **跨层 deep-import 治理**：`RAGChain` 仍 deep-import `tracer` / `evidence_gate`，未来用 DI 注入
- **路由层 god class（Violation E）**：`app/api/routes/rag.py` 974 行拆 3 文件（PR-2.2）
- **Skill 双注册通道残留**：`BaseSkill.__init_subclass__` 自动注册（ADR-0001 后续工作）
