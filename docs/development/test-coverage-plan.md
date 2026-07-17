# 测试覆盖补全计划

> **状态**：规划中（2026-07-17）  
> **目标**：把后端 pytest 覆盖率从 0% 提升到 ≥10%（P0 核心模块），前端 vitest 补充关键模块  
> **执行原则**：参考 [testing.md](./testing.md) — 正常/异常/边界/降级 四路径

---

## 现状（2026-07-17 体检）

| 模块 | 测试文件数 | 覆盖率 | 状态 |
|---|---|---|---|
| **backend/** | **0** | **0%** | 🔴 P1 |
| **frontend/src/** | 2 | ~3% | 🔴 P1 |
| Total | 2 | ~0.4% | 🔴 |

- **后端 224 个 .py 文件，0 个测试**
- requirements.txt 没有 pytest
- 前端只有 `traces/api.test.ts` 和 `trace.test.ts`

---

## P0：必须补的测试（按 CLAUDE.md 优先级）

### 🔴 P0.1 `backend/rag/tracer.py`（Trace 不能丢）

CLAUDE.md 明确：**Trace 丢失 = P0**。这是整个可观测性的根基。

**测试用例**：
- ✅ 正常路径：trace span 创建 → 嵌套 → 结束
- ✅ 异常路径：span 中抛异常 → 异常信息正确记录
- ✅ 边界：并发 100 个 span → 不丢失
- ✅ 降级：tracer 关闭时 → 不阻塞主流程

### 🔴 P0.2 `backend/rag/retrieval/`（RAG 核心 5 个文件）

| 文件 | 重点测试 |
|---|---|
| `retrievers.py` | ChunkLevelRetriever / AdaptiveRetriever 的 `_get_relevant_documents` |
| `hybrid.py` | `rrf_fusion_docs` 多路融合排序正确性 |
| `multi_query.py` | 多查询重写 + 去重 |
| `query_analyzer.py` | `ParsedQuery.to_metadata_filter` 类型转换 |
| `bm25_store.py` | add/search/remove/is_stale 四个公开方法 |

### 🔴 P0.3 `backend/orchestration/`（编排核心）

CLAUDE.md 强调：
- **Planner 只输出 capability DAG，禁止调 Tool/Skill/DB**
- **Skill 不直接 HTTP，通过 Tool**

| 文件 | 重点测试 |
|---|---|
| `state.py` | `StepResult` / `_merge_step_results` 状态合并 |
| `planner/` | Planner 输出验证：纯 DAG、无 IO 调用 |
| `supervisor/` | `supervisor_node` 路由决策 |
| `skills/base.py` | `BaseSkill._tool_fn` 包装正确性 + `execute_with_retry` |
| `graph/builder.py` | `route_after_planner` 路由逻辑 |
| `tool_registry.py` | Tool 注册 / 查找 |

### 🔴 P0.4 `backend/orchestration/skills/{rag,sql,report}/`

CLAUDE.md 强调 Skill 不直接 HTTP → 通过 Tool。需验证：
- `RAGSkill._tool_fn` 调 `search_knowledge_tool`
- `SQLSkill._tool_fn` 调 `sql_query_tool`
- `ReportSkill._tool_fn` 调 `generate_report_tool`

---

## P1：建议补的测试

### `backend/rag/preprocessing/`

| 文件 | 重点 |
|---|---|
| `chunking.py` | 切块边界（中英文混排） |
| `entity.py` | 实体抽取召回率 |
| `cleaner.py` | 文本清洗边界（特殊字符、空文档） |
| `loader.py` | 多格式加载（pdf/md/docx） |

### `backend/rag/indexing/`

| 文件 | 重点 |
|---|---|
| `indexer.py` | 增量索引 / 失败重试 |
| `doc_registry.py` | 文档状态流转 |

### `backend/rag/reranker.py`

- `RerankCompressor.compress_documents` 重排序 + 截断

---

## P2：长期优化

- `backend/business_report/` 报告生成（少改）
- `backend/data_collection/` 数据采集（pipeline 隔离）
- `backend/memory/` 记忆管理（依赖 PostgreSQL，测试 fixture 复杂）
- 前端 vitest 补 `src/lib/api/*.ts`、`src/components/*.tsx`

---

## 基础设施待办（执行前先建）

### 后端 pytest 框架

```bash
# requirements-dev.txt（新增）
pytest==8.3.0
pytest-asyncio==0.24.0
pytest-cov==5.0.0
httpx==0.27.0          # FastAPI TestClient 依赖
fakeredis==2.23.0      # Redis mock
mongomock==4.1.2       # MongoDB mock
```

```toml
# pyproject.toml 新增
[tool.pytest.ini_options]
testpaths = ["backend/tests"]
python_files = ["test_*.py"]
asyncio_mode = "auto"
addopts = "--cov=backend --cov-report=html --cov-report=term-missing"
```

### 目录结构

```
backend/tests/
├── conftest.py              # 全局 fixture（DB/Redis mock）
├── rag/
│   ├── test_tracer.py       # P0.1
│   ├── retrieval/
│   │   ├── test_retrievers.py
│   │   ├── test_hybrid.py
│   │   └── test_bm25_store.py
│   ├── preprocessing/
│   └── indexing/
├── orchestration/
│   ├── test_state.py        # P0.3
│   ├── test_planner.py
│   ├── test_supervisor.py
│   └── skills/
│       ├── test_rag_skill.py
│       ├── test_sql_skill.py
│       └── test_report_skill.py
└── shared/
    └── test_config.py
```

---

## 执行顺序建议

| 阶段 | 内容 | 工期估计 | 覆盖率目标 |
|---|---|---|---|
| **第 1 周** | 建 pytest 框架 + P0.1 tracer + P0.3 state | 3 天 | 0 → 5% |
| **第 2 周** | P0.2 retrieval 5 文件 | 4 天 | 5 → 8% |
| **第 3 周** | P0.4 skills 3 个 | 3 天 | 8 → 10% |
| **第 4 周** | P1 preprocessing + indexing | 4 天 | 10 → 15% |

---

## 验证方式（CLAUDE.md 要求）

1. **自动化**：每个 PR 必须 `pytest backend/tests` 全绿
2. **覆盖率门禁**：CI 阻断 < 10%
3. **手动验证**：新功能必须用户浏览器操作一遍（CLAUDE.md §15.1）

---

**为什么这份计划重要**：参考 [project-health-report](../../memory/project-health-report.md)，测试覆盖率 0.4% 是项目最大短板之一；补到 10% 才能支撑后续重构不破坏功能。