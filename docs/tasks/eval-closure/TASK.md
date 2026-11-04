# TASK: 评测闭环收口（eval-closure，DeepEval 体系补齐最简集）

> 立项于 2026-11-04。基于对 DeepEval 评测体系（3 类评测维度 + 5 步落地流程）与
> 本仓库真实链路的差距分析（file:line 证据见本文件「关键代码事实」）。
> 与 `eval-center-simplify`（进行中）的关系：该任务冻结区=门禁自动化/人工评分/Gate UI，
> 本任务正是其后续，不撞车但共用组件，需按「工作树策略」节排序。
> 接手总纲见 `docs/tasks/handoff-batches/NEXT-SESSION.md`（E 批=本文 B1-B3，F 批=本文 F1-F3）。

## 目标

补齐 DeepEval 体系下项目评测闭环的两类断点：
1. **接线断点**（纯前端，API/组件均已存在）：gate 审批、反馈候选晋升、人工评审无人能操作；
2. **能力断点**（后端小改）：表现指标只采集不裁决；线上流量无采样评测。

完成后 DeepEval 满足度：三类 3/3、五步 4.5/5（通用灰度回滚为唯一遗留，见冻结区）。

## 范围（6 项，用户已确认，勿扩大）

### 后端（3 处，无新迁移）

| # | 改动 | 文件 | 说明 |
|---|---|---|---|
| B1 | 表现阈值入门禁 | `backend/evaluation/release_gate.py` | `DEFAULT_THRESHOLDS`（:72-92）加 P95 时延/单例成本 CNY/降级率，复用现有 required/optional 三态机制，不加新判定分支 |
| B2 | 在线采样评测器 | `backend/evaluation/online_sampler.py`（新建单文件） | 按比例抽近期 trace（优先负反馈/低置信/失败终态），复用 `evaluation/judge.py` 打分，结果写现有 `ai.eval_run_records` + `ai.eval_run_samples`（迁移 056/075），`source=online`；零新表 |
| B3 | 采样调度 | Celery Beat 注册（`tasks/celery_app.py`） | 每日一次跑 B2，采样率读环境变量 |

### 管理端（3 个文件，全部纯接线）

| # | 改动 | 文件 | 挂接的既有资产 |
|---|---|---|---|
| F1 | Gate 状态列 + 审批按钮 + `online` 来源徽标 | `frontend-admin/src/components/workbench/evaluation/proto/RunsTab.tsx` | 字段 `gate_verdict/gate_reason`（api/evaluation.ts:296-297）；API `getV2Gate/createV2GateApproval`（:725-737） |
| F2 | 反馈候选审核（approve/reject/promote + 来源=feedback 过滤） | `frontend-admin/src/components/workbench/evaluation/proto/DatasetsTab.tsx` | API `reviewFeedbackCandidate/promoteFeedbackCandidate`（api/feedbackCandidates.ts:16-21） |
| F3 | 用例详情挂人工评审组件（带「不进自动门禁」标注） | `frontend-admin/src/components/workbench/evaluation/proto/CompareTab.tsx` | 组件 `EvaluationHumanReviewButton.tsx`（已实现未挂载） |

### 治理边界（分界裁决，勿越界）

- 管理端管「评测执行」，参数变更（采样率/阈值/灰度/CI）留环境变量与代码，不建配置页。
- 人工评审不进自动 gate（`evaluation_v2.py:278-279` 既有设计），UI 只标注、不改语义。

## 冻结区（本轮不做）

通用灰度发布/回滚框架、轨迹冗余指标、挑战集显式分层、EVAL_GATE legacy→semantic CI 切换、
CSAT/质检分→评测候选管道、travel 反馈接线、阈值/采样率管理端配置页、V1 API 收敛、死组件清理。

## 工作树策略（并行协调，2026-11-04 核对）

- **前置依赖**：`eval-center-simplify`（worktree `agent-eval-simplify`，Phase D 执行中）
  正在改 RunsTab/DatasetsTab/CompareTab 同三文件 → **F1-F3 必须等其 PR 合并后，
  从最新 origin/main 开分支实施，逐文件 diff 核对基线**。
- **可先行项**：B1/B2/B3（后端）与该并行任务无文件交集，可独立开工。
- 分支命名：后端批 `feat/eval-closure-backend`，前端批 `feat/eval-closure-admin-ui`，
  各自独立 PR 合并 main；主仓 `D:\Program Files\workplace\agent` 不直接改代码。
- 工作树：可复用 `agent-wt-audit-r1` 或新建，.env 缺失时从主仓拷贝。

## 测试

- B1：扩展 `release_gate` 相关既有测试 + 新增表现阈值判定用例（达标 PASS / 超线 FAIL / 缺数据 fail-closed）。
- B2/B3：sampler 单测（采样选择口径、judge 失败不吞、落库字段、source=online）；
  定向跑受影响面，不跑全量（testing-guide T0-T2）。
- F1-F3：组件测试挂接断言（按钮存在性、API 调用、角色显隐）+ `tsc` 通过。

## 验收

- [ ] B1：release_gate 对含表现指标的 suite 出三态裁决，阈值缺失 fail-closed；
- [ ] B2/B3：beat 触发一次采样 run 落库，`source=online` 可被 run 列表过滤；
- [ ] F1：super_admin 可在 RunsTab 完成 gate 审批闭环（API 实际调用成功）；
- [ ] F2：反馈候选可在 DatasetsTab approve/reject/promote（API 实际调用成功）；
- [ ] F3：CompareTab 用例详情可提交人工评审，页面标注「不进自动门禁」；
- [ ] 前端 tsc + 组件测试绿；后端定向测试绿；`devctl.bat rebuild` 健康检查通过；
- [ ] 未实际执行的验证不得报告为通过。

## 关键代码事实（设计依据，file:line 已核）

- gate 审批 API：`frontend-admin/src/api/evaluation.ts:725-737`，后端 `evaluation_v2.py:2107-2154`；后端人工评审 `evaluation_v2.py:1931-1955`（super_admin）
- 人工评审组件：`EvaluationHumanReviewButton.tsx:19-65`（生产 tab 未 import）
- 反馈候选 API：`api/feedbackCandidates.ts:16-21`（无任何 UI 调用方）；负反馈→候选管道 `feedback.py:121-140`
- gate 阈值唯一事实源：`release_gate.py:72-92 DEFAULT_THRESHOLDS`，无延迟/成本项
- judge：`evaluation/judge.py`（4 维 1-5 分，eval_gen 角色，judge golden 20 条护栏）
- 在线记录表：`ai.eval_run_records`（迁移 056:13）+ `ai.eval_run_samples`（075:16）
- eval-center-simplify 冻结区与其 TASK.md「本次不做」清单互证：本任务=其后续
