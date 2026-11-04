# PROGRESS: 评测闭环收口（eval-closure）

- 状态：**PLANNED**（任务已立项，未开工；接手总纲见 `docs/tasks/handoff-batches/NEXT-SESSION.md`）
- 工作树：待建/复用（见 TASK.md「工作树策略」）
- 基线：立项时 origin/main = `0a77b50`；本档案随 docs/handoff-next-batches PR 首次入库

## 并行协调记录

- 2026-11-04：撞车核对完成。`eval-center-simplify`（worktree `agent-eval-simplify`，
  Phase D IN_PROGRESS）冻结区=本任务范围，任务不撞车；但 F1-F3 与其共用三个组件文件，
  **前端批必须等其合并**。B1-B3 后端批无文件交集，可先行。
- 2026-11-04：审计 P3 收官批当日全部完成（PR #304-#316），本任务排在依赖批次之后，
  E 批（B1-B3）随时可开工。

## 进度

- [ ] B1 表现阈值入门禁（release_gate.py）
- [ ] B2 在线采样评测器（online_sampler.py）
- [ ] B3 Celery Beat 调度注册
- [ ] F1 RunsTab：gate 状态/审批/来源徽标（等 eval-center-simplify 合并）
- [ ] F2 DatasetsTab：反馈候选审核（同上）
- [ ] F3 CompareTab：人工评审挂载（同上）

## 阻塞

- 无硬阻塞。F1-F3 为软依赖：并行任务未合并前不动手。

## 下一步

1. E 批开工（B1→B2→B3，独立分支 `feat/eval-closure-backend`）；
2. 每次 F 批动工前 `git fetch` 核对 eval-center-simplify 是否已合并；
3. 完成项逐个勾选并附验证证据。
