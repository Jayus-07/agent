# ADR-0003: 目录分层重构（对齐 8 层架构）

| 项 | 值 |
|----|----|
| **状态** | Proposed |
| **日期** | 2026-08-04 |
| **作者** | wh |
| **影响范围** | `backend/` 全目录结构 + ~150+ import 路径 |
| **依赖** | ADR-0001（Skill 双注册表合并）· ADR-0002（RAGChain 拆解） |
| **被阻塞** | 等待 PR-1.2/1.3/1.4 完成（避免同时改业务 + 目录双重冲击） |

---

## 背景

`backend/` 当前目录结构在 160 个 commit 中自然演化而来，**未对齐 CLAUDE.md 定义的 8 层架构**。通过结构化盘点（`find backend -maxdepth 2 -type d`），发现 4 类结构性问题。

### 问题盘点

#### 1. Tool 层散落（最严重 🔴）

CLAUDE.md 定义 Tool 是独立层（无状态/可测试/必须 Trace），但当前 Tool 横跨 6+ 目录：

| 实际位置 | 实际 Tool |
|---------|-----------|
| `rag/retrieval/` | hybrid / multi_query / retrievers |
| `rag/preprocessing/` | keyword / metadata / cleaner / parser |
| `rag/indexing/` | indexer / chunking |
| `sql/` | sql_query / execute_sql |
| `mcp/servers/` | rag_server / sql_server |
| `memory/` | session_repository / long_term |
| `business_report/` | report_generator / template_engine |
| `data_collection/` | fetchers / parsers / writers |
| `evaluation/` | metrics / registry / runners |
| `seed/` | generators / importers / validators |

**症状**：
- 没有 `BaseTool` 统一基类，CLAUDE.md「Tool 必须 Trace」无强制
- 加新 Tool 不知道放哪（有人放 `rag/`，有人放 `sql/`，有人放新目录）
- `find_large_functions_tool` 在 Tool 层无法精准定位

#### 2. `rag/` 位置错（🔴）

`rag/chain.py`（RAGChain）和 `rag/evidence_gate/` 是 **Skill 的内部实现**，但放在 `rag/` 顶层：
- 与 CLAUDE.md「Skill 层 = `orchestration/skills/`」定义冲突
- 同样 7 个 Skill 中只有 `rag` 在 rag/，其他 6 个在 `orchestration/skills/` 下
- 历史包袱：rag 早于 orchestration 出现，先入为主

#### 3. `mcp/` Client + Server 混（🟡）

`backend/mcp/manager.py`（client）与 `backend/mcp/servers/rag.py`（server）同级：
- 跟 CLAUDE.md 8 层「MCP Client / MCP Server」分两层定义冲突
- 调用方 `from backend.mcp.servers import register_all` 与 `from backend.mcp import manager` 混用

#### 4. 缺 Service 层（🟡）

`backend/app/api/routes/chat.py` 174 行直接 import `AsyncSessionLocal` / `SessionRepository`（Violation B 调研结论）：
- 路由层调 ORM 会话 + repo，违反 CLAUDE.md「路由只做 HTTP 边界」
- 没有 `backend/services/` 目录
- `RAGChain.ask()` 是 Service，但放在 `rag/` 下不被识别

#### 5. 缺 Agent Runtime 独立目录（🟡）

CLAUDE.md 8 层写了 `Agent Runtime`（Router 之后、Planner 之前），但代码层不存在：
- `RAGChain` 越界当 Agent Runtime
- ADR-0002 拆解后，RAGChain 仅做 Skill 内部编排，Agent Runtime 仍是空白
- 未来要补（不在本次范围）

#### 6. 归属不清（🟢）

| 当前目录 | 应该是 | 原因 |
|---------|--------|------|
| `backend/evaluation/` | 独立 `dev_tools/evaluation/` | dev 工具，不是产品代码 |
| `backend/seed/` | 独立 `dev_tools/seed/` | 同上 |
| `backend/orchestration/inventory/` | 独立 `domain/inventory/` | 业务领域，非编排 |
| `backend/orchestration/reporter/` | 独立 `domain/reporter/` | 同上 |
| `backend/business_report/` | 归入 `domain/` 或 `tools/` | 业务领域 |
| `backend/memory/` | 归入 `tools/` 或 `services/` | 工具 |
| `backend/prompts/` | 已有（保留） | LLM prompt 模板 |
| `backend/shared/` `backend/config/` | 保留 | 基础设施 |

### 量化指标

- `backend/` 下子目录数：**27 个**（含 6 个 dev tools / 6 个 tools / 5 个 orchestration / 4 个 domain / 6 个 infra）
- 平均每目录被外部 import 次数：**12 处**（按文件数估算）
- 跨「错误层」的 import 数：**~30 处**（如 `rag/chain.py` → `orchestration/supervisor/alerts.py`）
- 若不重构：每加 1 个新 Tool 平均需要改 2-3 个 import 路径（散落难定位）

---

## 决策

**按 CLAUDE.md 8 层架构重组 `backend/` 目录，分 2 阶段迁移**：

### 阶段 A（必做）：核心 8 层对齐

| CLAUDE.md 层 | 新目录 | 旧目录 | 状态 |
|--------------|--------|--------|------|
| API | `app/` | `app/` | ✅ 不动 |
| Router | `app/api/routes/` | `app/api/routes/` | ✅ 不动 |
| Agent Runtime | `orchestration/agent_runtime/` | （无） | 🆕 新建 |
| Planner | `orchestration/planner/` | `orchestration/planner/` | ✅ 不动 |
| Supervisor | `orchestration/supervisor/` | `orchestration/supervisor/` | ✅ 不动 |
| Skill | `orchestration/skills/` | `orchestration/skills/` | ✅ 不动 |
| Tool | `tools/` | `rag/retrieval/` `rag/preprocessing/` `rag/indexing/` `sql/` `memory/` `business_report/` `data_collection/` `mcp/servers/`（仅 client 调用的） | 🔄 迁移 |
| MCP Client | `mcp/client/` | `mcp/manager.py` | 🔄 拆分 |
| MCP Server | `mcp/server/` | `mcp/servers/` | 🔄 重命名 |
| External Resource | `infra/` `db/` `external/` | `infra/llm/` | ✅ 保留 + 新建 |
| Service | `services/` | （部分在 `rag/chain.py` 等） | 🆕 新建 |
| Domain | `domain/` | `orchestration/inventory/` `orchestration/reporter/` `business_report/` | 🔄 迁移 |
| 基础设施 | `shared/` `config/` | 同 | ✅ 不动 |
| Dev tools | `dev_tools/` | `evaluation/` `seed/` | 🔄 迁移 |

### 阶段 B（可选）：Tool 基类与 Trace 强制

- 新建 `tools/base.py`：`BaseTool` 抽象类（无状态 + 自动 Trace span）
- 所有 `tools/*.py` 继承 `BaseTool`
- `BaseTool.execute()` 默认 `trace_collector.start_span("tool_call", kind=TOOL)`
- 这是**业务规则增强**，非纯目录迁移

---

## 备选方案

### 备选 A：维持现状 + 写 linter 禁止跨层

- 加 `directory_linter.py`：CI 拒绝 `rag/` → `orchestration/` 等跨层 import
- **否决理由**：治标不治本，新人仍找不到正确放 Tool 的地方；`rag/` 位置问题 linter 难表达

### 备选 B：只改 Tool 层（阶段 A 子集）

- 仅把 6+ Tool 目录合并到 `tools/`，不动其他层
- **优点**：工作量减半（~5 天 vs ~10 天）
- **否决理由**：遗留 `mcp/` 混合、`rag/` 错位问题，下次还是要解决

### 备选 C：完整阶段 A + 阶段 B（采用方案）

- 完整 8 层对齐 + Tool 基类
- **优点**：一次到位，新人 onboarding 清晰，CLAUDE.md 完全对齐
- **缺点**：工作量 1-2 周，所有 import 路径都改
- **缓解**：分 4 个子 PR 渐进迁移（见实施步骤）

---

## 设计细节

### 新目录结构（目标态）

```
backend/
├── app/                          # API + Router 层（不变）
│   ├── server.py
│   ├── api/
│   │   ├── routes/              # 路由（10 个文件，不变）
│   │   ├── middleware/          # 中间件（metrics/rate-limit）
│   │   ├── deps.py
│   │   └── router.py
│   └── exceptions.py
│
├── services/                    # 🆕 Service 层（业务编排）
│   ├── chat_stream_service.py   # 从 routes/chat.py 抽
│   ├── rag_query_service.py     # 从 rag/chain.py 抽
│   └── ...
│
├── orchestration/               # 多 Agent 编排
│   ├── agent_runtime/           # 🆕 Agent Runtime 层（CLAUDE.md 缺，本次只建空壳）
│   ├── planner/                 # Planner（不变）
│   ├── supervisor/              # Supervisor（不变）
│   ├── skills/                  # Skill（不变）
│   │   ├── rag/                 # 🔄 迁移：吸收原 rag/ 下 Tool 实现
│   │   │   ├── skill.py         # RAGSkill（已有）
│   │   │   ├── chain.py         # 🔄 从 rag/chain.py 迁入（PR-1.4 后）
│   │   │   ├── evidence_gate/   # 🔄 迁入
│   │   │   ├── retrieval/       # 🔄 迁入（from rag/retrieval/）
│   │   │   ├── preprocessing/   # 🔄 迁入
│   │   │   └── indexing/        # 🔄 迁入
│   │   ├── sql/  email/  report/  data_export/  web_search/  web_crawl/
│   ├── workflows/                # 复杂工作流（不变）
│   │   ├── daily_report.py
│   │   ├── inventory_alert.py
│   └── registry/                 # 🆕 tool_registry.py 等迁入
│
├── tools/                       # 🔄 Tool 层（无状态 + Trace）
│   ├── __init__.py
│   ├── base.py                  # 🆕 BaseTool 抽象类
│   ├── sql_query.py             # 🔄 从 sql/ 迁入
│   ├── execute_sql.py
│   ├── memory_session.py        # 🔄 从 memory/repository 迁入
│   ├── memory_long_term.py
│   ├── report_generator.py      # 🔄 从 business_report/ 迁入
│   ├── template_engine.py
│   ├── data_fetchers/           # 🔄 从 data_collection/fetchers/ 迁入
│   ├── data_parsers/
│   └── data_writers/
│
├── mcp/                         # MCP 层（拆分）
│   ├── client/                  # 🔄 从 mcp/manager.py 迁入
│   │   └── manager.py
│   ├── server/                  # 🔄 从 mcp/servers/ 重命名
│   │   ├── rag.py
│   │   ├── sql.py
│   │   └── __init__.py
│   └── __init__.py
│
├── domain/                      # 🆕 业务领域（独立于编排）
│   ├── inventory/               # 🔄 从 orchestration/inventory/ 迁入
│   ├── reporter/                # 🔄 从 orchestration/reporter/ 迁入
│   └── business_report/         # 🔄 从 business_report/ 迁入
│
├── db/                          # 🆕 数据库访问（独立 infra）
│   ├── models/                  # ORM 模型
│   ├── repositories/            # 仓储模式
│   └── migrations/              # Alembic
│
├── external/                    # 🆕 外部资源客户端
│   ├── llm/                     # 🔄 从 infra/llm/ 迁入
│   │   ├── provider_deepseek.py
│   │   ├── provider_minimax.py
│   │   ├── provider_ollama.py
│   │   └── rate_limiter.py      # (PR-0.4 已在)
│   ├── embedding/               # 嵌入模型
│   └── reranker/                # 重排序模型
│
├── infra/                       # 保留：通用基础设施
│   ├── tracer.py                # 保留
│   ├── metrics.py
│   └── ...
│
├── shared/                      # 跨层共享（不变）
│   ├── logger.py
│   ├── exceptions/
│   └── monitoring/
│
├── config/                      # 配置（不变）
│
├── prompts/                     # LLM prompt 模板（不变）
│
├── dev_tools/                   # 🆕 开发工具（不是产品代码）
│   ├── evaluation/              # 🔄 从 evaluation/ 迁入
│   ├── seed/                    # 🔄 从 seed/ 迁入
│   └── demo_runner/
│
├── tests/                       # 测试（不变）
└── __init__.py
```

### 关键映射表

| 旧 import | 新 import | 迁移方式 |
|----------|----------|---------|
| `from backend.rag.chain import RAGChain` | `from backend.orchestration.skills.rag.chain import RAGChain` | git mv + sed 替换 |
| `from backend.rag.evidence_gate import GateDecision` | `from backend.orchestration.skills.rag.evidence_gate import GateDecision` | 同上 |
| `from backend.sql.execute_sql import execute_sql_tool` | `from backend.tools.execute_sql import execute_sql_tool` | 同上 |
| `from backend.memory.repository.session import SessionRepository` | `from backend.db.repositories.session import SessionRepository` | 同上 |
| `from backend.mcp.servers.rag import register_all` | `from backend.mcp.server.rag import register_all` | 重命名 + 替换 |
| `from backend.mcp.manager import MCPClient` | `from backend.mcp.client.manager import MCPClient` | 拆分 + 替换 |
| `from backend.business_report.template_engine import render` | `from backend.tools.template_engine import render` | 迁入 tools |
| `from backend.evaluation.runners.builtin import run` | `from backend.dev_tools.evaluation.runners.builtin import run` | 迁入 dev_tools |

### BaseTool 抽象类（阶段 B 草图）

```python
# backend/tools/base.py
from abc import ABC, abstractmethod
from backend.infra.tracer import trace_collector, SpanKind

class BaseTool(ABC):
    """Tool 抽象基类（CLAUDE.md: Tool 必须 Trace + 无状态 + 可测试）。

    所有 Tool 继承此类，自动获得 Trace 埋点。
    """
    name: str = ""
    description: str = ""

    @abstractmethod
    def execute(self, *args, **kwargs):
        """子类实现：纯函数式业务逻辑（无 this 状态）。"""
        ...

    def __call__(self, *args, **kwargs):
        span = trace_collector.start_span(
            f"tool_{self.name}",
            name=self.description or self.name,
            kind=SpanKind.TOOL.value,
        )
        try:
            result = self.execute(*args, **kwargs)
            trace_collector.end_span(span, status="success")
            return result
        except Exception as e:
            trace_collector.end_span(span, status="error",
                                     metrics={"error_type": type(e).__name__})
            raise
```

---

## 影响

### Import 路径改动统计（预估）

| 类别 | 改动数 |
|------|--------|
| 业务代码 import 路径 | ~150 处 |
| 测试代码 import 路径 | ~60 处 |
| 配置文件 | ~5 处（pyproject / Dockerfile / pytest） |
| **合计** | **~215 处** |

### 工作量

| 阶段 | 内容 | 工作量 |
|------|------|--------|
| 阶段 A1 | `rag/` → `orchestration/skills/rag/` | 2 天 |
| 阶段 A2 | Tool 目录合并（`tools/`） | 2 天 |
| 阶段 A3 | `mcp/` 拆分 + `domain/` 抽离 | 1 天 |
| 阶段 A4 | `db/` `external/` 抽离 | 1 天 |
| 阶段 B | `BaseTool` 抽象类 + 迁移 | 2 天 |
| **合计** | | **8 天** |

### 兼容性

| 维度 | 影响 |
|------|------|
| 公共 API（`/chat` `/rag/query` 等） | 不变（路由层不重构） |
| MCP 协议 | 不变（server 端协议兼容） |
| 配置文件路径 | `backend/config/llm.py` 等不变 |
| 部署（Docker） | Dockerfile 路径要更新 |
| 前端 API 调用 | **不变**（HTTP 边界） |

### 风险

| 风险 | 缓解 |
|------|------|
| 改 200+ import 路径漏改 | 写 `migrate_imports.py` 脚本，sed + 验证；验收脚本新增 `--only=structure` 检查 |
| 阶段 A1 与 ADR-0002 冲突 | **先完成 PR-1.4**（RAGChain 瘦身后）再做 A1 |
| 测试 fixture `_REBIND_MODULES` 失效 | 迁移时同步更新 fixtures 列表 |
| 部署 / CI 配置不同步 | 阶段 A4 末尾更新所有 Dockerfile / pyproject |
| 工作量大导致中途放弃 | 分 4 个子 PR，每个 PR 验收后再进行下一个 |

---

## 验证标准

### 阶段 A 完成验收（核心）

#### 静态验证（PR-3.3 脚本扩展）

- [ ] `scripts/verify_refactor_2026q3.sh --only=static` 全绿
- [ ] 新增 `--check=structure`：每个新目录至少有 1 个 .py 文件
- [ ] `grep -rn "from backend.rag" backend/orchestration/skills/rag/` 0 处（rag 内部不外引）
- [ ] `grep -rn "from backend.orchestration" backend/tools/` 0 处（Tool 不依赖 Skill/Orchestration）
- [ ] `grep -rn "from backend.business_report" backend/` 0 处（旧目录无残留）

#### 单元 + 集成测试

- [ ] `pytest tests/ -q` 整体仍 485+ passed（不允许新失败）
- [ ] `pytest tests/test_adr0001_dual_registry_merge.py` 通过（Skill 注册表仍工作）
- [ ] `pytest tests/test_adr0003_directory_layering.py` 新增（迁移正确性）

#### 端到端

- [ ] 4 个 demo 场景（daily_report / inventory_alert / rag_qa / knowledge_index）跑通
- [ ] `curl /metrics` 返回正常（Prometheus 端点不变）
- [ ] `curl /health` 返回 200

### 阶段 B 完成验收（Tool 基类）

#### 行为正确性

- [ ] 任意 Tool 调用 → Trace span 包含 `tool_{name}` kind=TOOL
- [ ] Tool 抛异常 → Trace span status=error, metrics.error_type 有值
- [ ] BaseTool.execute 抽象方法未实现 → 启动时 TypeError（与 Skill 校验一致）

#### 兼容性

- [ ] 现有 8 个 Skill 内部使用的 Tool 全部可调用
- [ ] `from backend.tools.base import BaseTool` 公开 API 不变

#### 文档

- [ ] `docs/architecture/structure.md` 更新到新目录树
- [ ] ADR-0003 状态 `Proposed` → `Accepted`（阶段 A 完成时）
- [ ] CHANGELOG 记录大版本迁移

---

## 实施步骤

### 前置条件

- ✅ ADR-0001 已合并（Skill 双注册表）
- ✅ ADR-0002 PR-1.1 已合并（EvidenceGateController 抽出）
- ⏳ ADR-0002 PR-1.2/1.3/1.4 待完成（RAGChain 瘦身到 ~300 行）

### 4 个子 PR

| PR | 范围 | 工作量 | 验收 |
|----|------|--------|------|
| **PR-3.4** 阶段 A1: `rag/` → `orchestration/skills/rag/` | 1 个大目录迁移 | 2 天 | grep + 单元测试 |
| **PR-3.5** 阶段 A2: Tool 目录合并 | 6+ 目录 → `tools/` | 2 天 | 8 个 Tool 仍可调用 |
| **PR-3.6** 阶段 A3+A4: `mcp/` 拆分 + `db/` `domain/` `external/` 抽离 | 4 类迁移 | 2 天 | e2e demo 全过 |
| **PR-3.7** 阶段 B: BaseTool 抽象类 | 所有 Tool 改继承 | 2 天 | span.kind=TOOL 验证 |
| **合计** | | **8 天** | |

### 每个子 PR 模板

1. 写 `migrate_<step>.py` 脚本（git mv + 替换 import）
2. 在测试环境跑 `python migrate_<step>.py --dry-run`，人工 review diff
3. 真跑：commit + 跑全量测试
4. 如失败：回滚到上一个 commit
5. 通过：标 PR 完成，进下一个

---

## 后续工作

- **Agent Runtime 独立化**（CLAUDE.md 缺层）：本次只建空壳，未来补实现
- **跨服务边界（API 网关 / Sidecar）**：等目录稳定后讨论
- **Monorepo 化**：`backend/` + `frontend/` → 单一 pyproject + workspace
- **ADR-0004 候选**：领域驱动设计（DDD）限界上下文划分

---

## 元决策记录

本 ADR 涉及 215+ import 路径改动，是项目至今**最大规模的非业务改动**。决策前明确：
- 不在 PR-1.2/1.3/1.4 期间执行（避免双重冲击）
- 不引入新依赖（仅 Python 标准库 + 现有工具）
- 不破坏 HTTP / MCP 公共协议
- 失败可分 PR 回滚（每个子 PR 独立）
