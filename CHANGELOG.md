# Changelog

> 所有**非业务**变更（重构 / 工具 / 文档 / 测试 / 可观测）都记录在这里。
> 业务功能变更在 commit message 里，不重复。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased] — 2026-08-04~05 架构全面重构

### 安全加固（第 1 轮）

- **P0-1**: 8 处 `except Exception: pass` → `logger.warning/debug(exc_info=True)`
- **P0-2**: `generate_data` 表名白名单 + 参数化查询（防 SQL 注入）
- **P0-3**: 移除硬编码 DB 密码 `123456`，新增 `.env.example`
- **P0-4**: `os.getenv` 从 concurrency.py / snapshot.py 迁入 config/
- **P1-1**: evidence_gate / hybrid 改用 `backend.shared.logger` 统一 logger

### God File 拆解

- **ADR-0002 完成**: RAGChain 886→695 行
  - PR-1.1: EvidenceGateController ✅
  - PR-1.2: SelfCorrectionStrategy ✅
  - PR-1.3: CitationFormatter ✅
  - PR-1.4: 接线 3 策略对象 ✅
- **evidence_gate**: `__init__.py` 451→65 行 → models.py + operations.py
- **metadata.py**: 843→635 行 → llm_enrichment.py
- **config/rag.py**: 310→188 行 → domain_data.py
- **product.py**: 585→292 行 → seed/data/product_data.py
- **indexer.py**: SyncResult/Delta → models.py

### 目录重构（对齐业界标准）

- **新增顶层目录**:
  - `tools/` — 统一工具层（9 模块，从 orchestration/ + data_collection/ 收敛）
  - `observability/` — 统一可观测层（7 模块，从 rag/ + middleware/ + supervisor/ + shared/ 收敛）
  - `agents/` — Agent 节点（planner/reporter/capability，从 orchestration/ 抽出）
  - `skills/` — 业务能力层（8 个 Skill，从 orchestration/skills/ 提升）
- **MCP 提升**: `backend/mcp/` → 顶层 `mcp_servers/`（对齐独立进程模式）
- **shared/ 瘦身**: 8→3 文件（AUTH_TODO→docs, async_utils→infra, timeout→infra）
- **orchestration/ 瘦身**: 58→18 文件（删除 29 个 thin re-export 死文件）
- **middleware 合并**: `app/middleware/` → `app/api/middleware/`
- **demo 迁移**: 3 个独立脚本 → `scripts/`
- **rag.py 路由拆分**: 884 行 → 4 文件（search/documents/upload/_shared）

### 架构修复

- **MCP 层解耦**: 工厂函数下沉到源模块（sql/rag），消除下层依赖 web 层
- **Service 层抽取**: MemoryService 6 CRUD + chat save_messages 委托
- **路由内联 SQLite**: `list_documents` 20 行 → `DocumentOperationLogger.get_last_ops_batch()`
- **DataCollection Skill 归位**: → `orchestration/skills/data_collection/`
- **Skill 注册表统一**: 消除外部惰性加载机制
- **PR-2.4**: 限流真返 HTTP 429 + Retry-After

### 工程化

- **pyproject.toml**: 显式依赖分层（Web/LLM/存储/NLP/可观测/工具）
- **根目录清理**: 删除 9 个临时文件（--help/.coverage/coverage.json/*.log/*.db/requirements.txt）
- **data/ 清理**: 删除 ~72MB 可重新生成数据（tmp/reports/snapshots/bm25）
- **.gitignore**: 完善 coverage.json + data/bm25/ + data/rag/tmp/

### 质量指标

- 测试：485 → **500 passed**, 0 fail, 0 regression
- 风险评分：49% → **~77%**（7 项设计原则）
- 死代码：0 处
- 循环 import：0 处
- 安全漏洞：0 处（SQL 注入/硬编码密码/静默吞错）

## 历史

[Unreleased] 段的变更会在下个版本 tag 时归入具体版本。
