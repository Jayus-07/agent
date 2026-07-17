# Dead Code 候选清单（2026-07-17）

> **来源**：`code-review-graph refactor_tool --mode dead_code`
> **生成时间**：2026-07-17（graph 重构后）
> **总数**：438 符号 → 过滤 node_modules/.next/htmlcov → **294 真业务符号**

---

## 过滤分类

| 类别 | 数量 | 处理 |
|------|------|------|
| **node_modules / .next / htmlcov** | 162 | 跳过（图谱噪音） |
| **FastAPI 路由误报**（`@router.get/post` 装饰器） | 53 | 跳过（前端调用，删了 = 404） |
| **真疑似死代码**（backend 103 + frontend 88） | 191 | **待人工核对，逐个删** |
| **工具/类**（7 个 Class） | 7 | 待核对 |

---

## 误报分类（为什么静态分析找不到调用方）

Graph 不知道这些调用模式：

| 模式 | 例子 | 真实调用方 |
|------|------|-----------|
| **FastAPI 装饰器** | `chat.py::chat_stream` | 前端 `fetch('/api/chat/stream')` |
| **Pydantic validator** | `fetchers/base.py::fetcher_type` | pydantic 反射调用 |
| **Module-level 调用** | `mcp/servers/__init__.py::register_all` | `import` 时自动触发 |
| **SSE generator** | `chat.py::producer` | `StreamingResponse(producer())` |
| **生命周期 hook** | `memory/manager.py::_init_loop` | `register_shutdown(_shutdown)` |
| **Tool 注册函数** | `data_collection/tool.py::data_collection_tool` | LangGraph ToolRegistry 扫描 |
| **类方法被反射调** | `evaluation/registry.py::list_registered` | `getattr(registry, "list_registered")` |

---

## 真疑似死代码 Top 30（按文件分组）

> 全部列在 docs/development/dead-code-full-list.md（本次未生成，避免大文件）

### backend/app/api/routes/ (路由附近)
- `chat.py:97 producer` — SSE generator，需要看是否被 StreamingResponse 调用
- `rag.py:308 indexed_with_progress` — 可能 helper（待确认）

### backend/business_report/
- `report_generator.py:265 init_report_agent` — 可能启动 hook
- `template_engine.py:467 reload` — 可能 watcher callback

### backend/data_collection/
- `fetchers/base.py:33 fetcher_type` — Pydantic discriminator（误报）
- `parsers/base.py:31 supports` — 同上（误报）
- `scheduler.py:68 run_now` — 可能 CLI 入口
- `scheduler.py:96 list_jobs` — 可能 CLI 入口
- `tool.py:62 data_collection_tool` — Tool 注册（误报）

### backend/evaluation/
- `judge.py:14 set_llm_callable` — 测试辅助？
- `metrics.py:71 exact_match` — Metric 注册（误报）
- `metrics.py:86 sort_key` — 同上
- `registry.py:37 list_registered` — 反射调用（误报）
- `registry.py:42 clear_registry` — 测试 helper？
- `report.py:80 write_json_report` — CLI 入口？
- `report.py:125 compare_reports` — CLI 入口？

### backend/infra/llm/
- `factory.py:87 get_current` — 可能 deprecated
- `proxy.py:150 wrapper` — 装饰器生成函数（误报）

### backend/mcp/servers/
- `__init__.py:10 register_all` — Module-level（误报）

### backend/memory/
- `database.py:49 get_session` — DB session helper（可能在 lifespan）
- `database.py:61 init_db` — 同上
- `long_term.py:46 embedding` — 可能 helper
- `manager.py:27 _init_loop` — 生命周期（误报）
- `manager.py:43 _shutdown` — 生命周期（误报）
- `pii_filter.py:120 has_pii` — 可能是 Pydantic validator
- `repository/memory_repo.py:74 find_by_id` — DB 查询

---

## 清理策略（按 CLAUDE.md P1 节奏）

> **不一次性删 191 个**。理由：风险不可控，且大量是静态分析盲区。

### 节奏
1. **每个 PR ≤ 10 个**
2. **优先删没有动态调用的 helper**（不是注册/装饰器/hook）
3. **每次删前用 `query_graph_tool --pattern callers_of` 二次确认**
4. **删完跑 pytest + detect_changes_tool**

### 本次（chore/test-coverage-and-mcp-fix）**不删**
原因：
- 工作量超出 PR 边界（应单独 PR）
- 误报分类需要逐个核对（不能批量）
- pytest 框架刚搭建，需要稳定基线

### 下一步（建议另起 PR）
1. 先挑 5-10 个**明显是 helper 且无动态调用**的（如 `exact_match` / `sort_key` 如果确认是 Metric 注册入口，则跳过）
2. 删完后跑 `pytest backend/tests` 确认没破坏功能
3. 提交 PR 后再继续下一批

---

## 验证脚本

```bash
# 重新跑 dead_code 检测（graph 更新后会变）
.venv/Scripts/python.exe -m code_review_graph refactor --mode dead_code

# 人工核对单个符号的 callers
# 用 mcp__code-review-graph__query_graph_tool --pattern callers_of --target <name>

# 删除前最后一道防线
.venv/Scripts/python.exe -m pytest backend/tests -v
```