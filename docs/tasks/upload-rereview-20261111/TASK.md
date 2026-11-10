# TASK: RAG 上传链路复审（batch3，新会话接手）

> 交接来源：2026-11-10 会话（三轮修复 batch0/batch1/batch2 已合并部署）。
> 排期权威：docs/tasks/upload-followups-20261110/TASK.md（batch3~6 计划见其「执行批次计划」节）。

## 目标

以新鲜眼对 RAG 上传链路做**独立复审**（不信任既有结论，逐段重读代码验证），产出评审报告；确认既有修复无新引入缺陷，发现遗留/新问题并排入后续批次。

## 复审范围（按序）

1. **后端**：backend/app/api/routes/rag_upload.py（~2140 行，核心）→ _rag_shared.py → indexing/publish.py + index_run_store_pg.py（候选/发布/幂等）→ middleware/body_limit.py → config/rag.py 常量
2. **三端 BFF**：frontend{,-admin,-frontend-cs}/src/app/api/[...path]/route.ts（三份相似文件，历史漂移两次——重点核对白名单/上限/流式是否一致）
3. **admin 前端（活 UI）**：frontend-admin/src/components/knowledge/UploadDialog.tsx + api/knowledge.ts + workbench/knowledge/DocumentsPanel.tsx 接线
4. **边界**：SSE 双通道路由（进程内/Redis 轮询）、幂等键语义、租户/部门授权、清理 sweeper

## 验收

- 评审报告（可写回本目录 PROGRESS.md）：逐项带 file:line 证据与风险分级（P1 功能性 / P2 缺陷或体验 / P3 增强 / P4 观感）
- 已知修复点抽查表（PROGRESS.md 里有 8 处清单）全部在当前 main 验证仍生效
- 不在本批改码；发现项按 followups TASK.md 批次原则排期

## 明确不做

- 不跑全量回归（testing-guide 口径，按需定向）
- 不动其他会话的未跟踪文件（主仓库根有 docs/tasks/.mimosa、trace-* 等未跟踪目录）
- #3 状态码版本化不实施（batch6，需 ADR）
