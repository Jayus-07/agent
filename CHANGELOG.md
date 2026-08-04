# Changelog

> 所有**非业务**变更（重构 / 工具 / 文档 / 测试 / 可观测）都记录在这里。
> 业务功能变更在 commit message 里，不重复。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### 重构（按 ADR）

- **ADR-0003 · 目录分层重构提案**（Proposed）
  路径：`docs/architecture/adr/0003-directory-layering.md`
  范围：~215 处 import 路径改动，4 个子 PR，8 天工作量
  状态：PR-1.4 已完成，可开始实施（4 个子 PR，8 天工作量）

- **ADR-0002 · 拆解 RAGChain god class**（✅ Accepted — 2026-08-04）
  路径：`docs/architecture/adr/0002-ragchain-decomposition.md`
  - PR-1.1: 抽 `EvidenceGateController`（✅ 2026-08-04）
  - PR-1.2: 抽 `SelfCorrectionStrategy`（✅ 2026-08-04）
  - PR-1.3: 抽 `CitationFormatter`（✅ 2026-08-04）
  - PR-1.4: `RAGChain` 瘦身为 orchestrator — 接线 3 策略对象（✅ 2026-08-04）
  结果：886 行 → 695 行（-191），删除 4 个模块级函数 + 3 个方法 + 5 个字段

- **ADR-0001 · 合并 Skill 双注册表**（Accepted，已合并）
  路径：`docs/architecture/adr/0001-merge-dual-registry.md`

### P0 缺口修复（按生产化路线图）

- **PR-0.3 · Prometheus `/metrics` 端点**（✅ 2026-08-04）
  新增 `backend/app/api/middleware/metrics.py`（4 metric）
  测试：7/7 通过

- **PR-0.4 · LLM 限流（TokenBucket + 仅日志）**（✅ 2026-08-04）
  新增 `backend/infra/llm/rate_limiter.py`（双层：global + per-user）
  测试：9/9 通过

### 测试基础设施

- **PR-0.1 · 修 tracer 测试 fixture 兼容 2d627d7 重构**（✅ 2026-08-04）
  新增 `backend/tests/fixtures/sqlite_tracer.py`（公共 `fresh_collector` fixture）
  关键修复：
  - tracer 公共 API：`clear_for_test()` / `current()` / `list(include_spans=True)`
  - `_REBIND_MODULES` 自动 patch（防业务模块漏改 fixture）
  - 48 errors + 20 failed → 0 红（485 passed, 1 skipped）

### 工具

- **PR-3.3 · 重构验收脚本**（✅ 2026-08-04）
  新增 `scripts/verify_refactor_2026q3.sh`（6 类验收：静态 / 单元 / 集成 / e2e / 性能 / 文档）
  支持 `--only=static|unit|integration|e2e|perf|docs` 子集
  最新运行：13 pass / 5 warn（预期） / 0 fail

## 历史

[Unreleased] 段的变更会在下个版本 tag 时归入具体版本。
