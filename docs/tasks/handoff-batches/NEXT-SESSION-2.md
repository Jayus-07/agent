# 接手计划（NEXT-SESSION-2，2026-11-05 写）

> 取代 `NEXT-SESSION.md`（其已知信息/教训/挂起裁决仍然有效，本文只增量）。
> 本轮完成 **E 批（eval-closure 后端 B1-B3）全部代码与定向测试**，
> 收尾（commit/PR/CI/合并/rebuild）留给下一会话；之后按 D1 → F → D3 → D4a 推进。
> **用户已裁决（继承）：ECS 三项不处理；pillow/filelock/dnspython/unstructured
> 因 py3.11 不可达保持挂起勿翻案。**

## 一、已知信息（本轮新增，免重探）

### E 批已完成基线（worktree 未提交状态，见「六、工作树」）

| 项 | 文件 | 内容 |
|---|---|---|
| B1 | `backend/evaluation/release_gate.py` | `DEFAULT_THRESHOLDS` 加 `p95_latency_ms=30000 / cost_per_case_cny=1.0 / degradation_rate=0.05`（全部 direction=lower）；三项进 `LOWER_IS_BETTER`/`GATE_METRIC_LABELS`/`METRIC_SOURCE`；进 **RAG 与 SQL policy 的 optional**（有值裁决超线 FAIL、缺失 N/A 不影响存量 gate——required 会把所有现存 suite 干成 FAIL，勿改成 required） |
| B2 | `backend/evaluation/online_sampler.py`（新建） | `run_online_sampling()`：trace_store 权威源采样 → judge 打分 → `ai.eval_run_records`（trigger='online'）+ `ai.eval_run_samples`（evaluator='judge'）落库 |
| B3 | `backend/tasks/eval_sampling_tasks.py`（新建）+ `celery_app.py` include/beat_schedule + `queue_router.py` 登记 | task `evaluation.online_sampling`，每日 01:30 UTC，queue=report（beat_queue 派生） |
| 测试 | `tests/evaluation/test_release_gate_module_aware.py`（+93 行 7 用例）、`tests/evaluation/test_online_sampler.py`（新 13 用例） | **41 passed**（原 40 条基线 + Beat 注册断言）；路由/拓扑守卫 `test_phase2_runtime_contract + test_task_queue_router + test_worker_topology` **48 passed** |

### 关键设计事实（本轮核实，含踩坑）

1. **在线 run 走 legacy 台账而非 v2**：`evaluation_v2.eval_runs.suite_version_id NOT NULL`
   + create_run 需要 suite/dataset/语料快照全链——采样场景没有 suite。故 B2 写
   `ai.eval_run_records`（`trigger='online'` 为过滤口径，056 注释枚举加一值）。
   v2 RunsTab（F1）的 online 徽标绑 `run.source==='online'` 纯展示位，数据有无由后端决定。
2. **trace 权威源 = `public.trace_store`**（PostgresTraceStore，`OBS_DB_PG_TABLE_PREFIX` 前缀，
   本机实测 1195 行真实数据）；`ai.trace_records` 是 pg_trace_sink 镜像且
   `TRACE_PG_MIRROR_ENABLED` 默认 false（空表勿误判无数据）。查询复用
   `trace_store.list_since(cutoff_iso, limit)`（已按 created_at DESC）。
3. **feedback 表在 `agent_business` 库**（`FEEDBACK_PG_CONFIG`，public.feedback，
   非 agent_memory！），有 `trace_id` 列——负反馈直接 `SELECT DISTINCT trace_id
   WHERE vote='negative' AND created_at >= cutoff`（UTC 文本字典序比较）。
4. **trace 三个优先信号全部现成**：`status`（error/cancelled/degraded/rejected/success）、
   `metadata.degraded`+`degraded_reasons`（降级）、feedback.vote='negative'。
   「低置信」无独立字段（rejection.scores 语义未核实），用 degraded 替代，如需补先核实。
5. **judge 契约**：`judge_answer(question, rubric, answer)` 永不抛（catch-all 返
   total=0.0）；0.0 → status='error' 计数不吞、不进均值（与 rag.py:843 先例一致）。
   及格线 judge_total≥3.0（60 分）→ pass。eval_gen 未绑定模型时全部 0.0——
   生产首次开 `EVAL_ONLINE_SAMPLER_ENABLED=true` 前先确认 eval_gen 已绑模型。
6. **幂等**：run_id 按天 `online-YYYYMMDD`；records ON CONFLICT 更新 run 摘要，
   样本明细在同一事务内先清理该 run 的旧 judge 行、再写当前行集，避免同日重跑
   后摘要计数与样本明细不一致。
7. **采样开关与参数（env，治理边界：不建配置页）**：
   `EVAL_ONLINE_SAMPLER_ENABLED`（默认 false）、`EVAL_ONLINE_SAMPLE_RATE`（0.05）、
   `EVAL_ONLINE_SAMPLE_MAX`（20）、`EVAL_ONLINE_SAMPLE_WINDOW_HOURS`（24）。
8. **B1 判定路径**：`_evaluate_requirement` optional+有值+阈值（DEFAULT 恒有）→
   判定；三项放 optional 是向后兼容唯一可行解，TASK.md 验收「缺数据 fail-closed」
   由 required 既有铁律覆盖（守卫用例 test_perf_optional_does_not_mask_required_fail）。

### 环境与操作纪律（全部继承 NEXT-SESSION.md 第一节）

代理（git push/gh 带 `HTTPS_PROXY=http://127.0.0.1:7897`）、devctl rebuild 用
`python -c subprocess` 包 cmd.exe（timeout ≥7000s）、PostgreSQL 显式 5433
（容器用户 postgres/agent_memory）、Mimosa hook 拦 Bash heredoc 写文件（走 Write/Edit）、
py3.10 铁律。**本轮新坑**：Bash 里 `sed -n` 纯读取 + grep 组合也可能被 hook 误判为
写源码——读文件优先用 Read 工具，grep 单独发。

## 二、总计划（剩余工作，按优先级）

| # | 内容 | 状态 | 前置 |
|---|---|---|---|
| E' | E 批收尾：commit → push → PR → CI 全绿 → 合并 → 主仓 pull → rebuild → 健康确认 | **代码就绪，收尾未做** | 无 |
| D1 | dev-only 124 包语义复检（文档批，diff 最新版评估 breaking changelog） | 未开工 | 无 |
| F | eval-closure 前端三项（F1 RunsTab gate 列/审批按钮/online 徽标；F2 DatasetsTab 反馈候选审核；F3 CompareTab 人工评审挂载） | 未开工 | ~~eval-center-simplify 已合并~~ **已满足**（95f3c4d/497c909 已在 main，worktree 已清理；F 批开工前仍需逐文件 diff 核对基线，因 9c1ca42/e1268cb 11-02 又改过三 tab） |
| D3 | langchain/langgraph 整族升级（目标组合见 NEXT-SESSION.md） | 未开工 | 独立批 |
| D4a | cryptography 50 / datasets 5.0.1 / oauthlib 4.0 跨大版本 | 未开工 | 独立批+定向回归 |

## 三、E' 收尾步骤（下一个会话第一件事）

1. worktree `D:\Program Files\workplace\agent-wt-audit-r1`（分支 `feat/eval-closure-backend`）
   先 `git status` 核对 7 个文件改动仍在（见一节表格）+ 复跑定向测试确认基线：
   `cd backend && D:/Python/python.exe -m pytest tests/evaluation/test_online_sampler.py
   tests/evaluation/test_release_gate_module_aware.py -q`（当前预期 41 passed；原始 40 条
   基线已复跑，之后增加 Beat 时间/队列注册回归断言）。
2. 按 AGENTS.md 提交规范分一次 commit（一个提交一件事，全部文件同属 eval-closure 后端批）：
   `git add -A -- backend/evaluation/online_sampler.py backend/evaluation/release_gate.py
   backend/tasks/eval_sampling_tasks.py backend/tasks/celery_app.py backend/tasks/queue_router.py
   backend/tests/evaluation/test_online_sampler.py backend/tests/evaluation/test_release_gate_module_aware.py
   docs/tasks/handoff-batches/NEXT-SESSION-2.md`
   然后 `git commit -m "feat(evaluation): eval-closure 后端三项——表现阈值门禁/在线采样评测/每日调度" -- <同上路径>`（双重路径限定）。
3. push（带代理）→ 开 PR（base main，说明含测试证据）→ CI 全绿 → 合并。
4. 主仓 pull → `devctl.bat rebuild /y`（subprocess 包 cmd.exe，timeout ≥7000s）→
   容器健康 + `beat` 容器日志确认 `evaluation.online_sampling` 注册
   （默认关：`EVAL_ONLINE_SAMPLER_ENABLED` 未开时任务每天空跑返回 skipped）。
5. 合并后主仓 `git pull` 并核对 NEXT-SESSION-2.md 已入库。

**未完成原因**：本轮会话在定向测试全绿后到达交接边界；commit/PR 需按协作规范
先向用户展示 Diff 求确认，由下一会话连同 D1 批次一起推进，避免半途提交。

## 四、D1 步骤（E' 合并后可立即做，文档批）

1. `pip index versions <pkg>`（或 PyPI JSON API）对 requirements-lock.txt 里 dev-only
   124 包逐个 diff 最新版本；仅深查「有新版且 changelog 含 breaking」条目。
2. 产出复检结论表落 `docs/tasks/audit-remediation-r1/PROGRESS.md` 增量（或独立小文档）；
   无实际问题记「复检通过，无需变更」；发现真问题才开代码批。

## 五、F 批要点（前置已满足，开工前再核对）

- 分支 `feat/eval-closure-admin-ui` 从最新 origin/main；worktree 可复用 audit-r1 或新建（拷 .env）。
- F1 `RunsTab.tsx`：gate 状态列绑 `gate_verdict/gate_reason`（api/evaluation.ts:296-297）、
  super_admin 审批按钮（`getV2Gate/createV2GateApproval` :725-737）、online 来源徽标
  （`run.source==='online'` 展示位）；F2 `DatasetsTab.tsx`：
  `reviewFeedbackCandidate/promoteFeedbackCandidate`（api/feedbackCandidates.ts:16-21）
  + 来源=feedback 过滤；F3 `CompareTab.tsx`：挂 `EvaluationHumanReviewButton`
  + 「不进自动门禁」标注。全部纯接线，不新增页面/权限面。
- 验证：tsc + 组件测试 + rebuild 后真实页面走查（super_admin 审批闭环 API 实际调用成功）。

## 六、工作树

- `D:\Program Files\workplace\agent-wt-audit-r1`（分支 `feat/eval-closure-backend`，
  基于 origin/main e7d7325；**8 个实现文件 + 本交接稿，共 9 个文件未提交**：5 M + 3 新增；根 .env 在位、backend/.env
  主仓也没有——DB 测试走容器内 5433 环境变量，无需 backend/.env）。
- E' 提交并合并后该 worktree 可继续承载 D1/F 分支（每批一次独立分支）。
- 不动其他会话 worktree；开工前 `ls docs/tasks/` + `git fetch` 防撞车
  （本轮核对：trace-probe-isolation / trace-query-pushdown / trace-ux-pack 为其他会话在途，与本计划无交集）。

## 七、测试与验收（继承八步链 + E 批专项）

- 依赖批（D3/D4a）固定八步链：① requires_python 逐包 → ② OSV 清零 → ③ 改 lock →
  ④ dry-run 全量解析 → ⑤ PR CI → ⑥ 合并 → ⑦ pull+rebuild → ⑧ 容器 freeze 一致性+冒烟+/health 双 200。
- E 批验收（TASK.md）：B1 三态裁决+缺数据 fail-closed（原 40 条基线复跑；加入 Beat
  注册断言后当前 41 passed）✓（代码面）；
  B2/B3 beat 触发一次采样 run 落库且 trigger='online' 可过滤——**rebuild 后需实机验证**
  （临时开 `EVAL_ONLINE_SAMPLER_ENABLED=true` 手动触发任务一次，查
  `SELECT run_id, trigger, metrics FROM ai.eval_run_records WHERE trigger='online'`，
  验完关闭还原）。
- F 批验收：三 tab 各自 API 实际调用成功（super_admin gate 审批闭环、候选
  approve/reject/promote、人工评审提交）+ tsc/组件测试绿。
- 未实际执行的验证不得报告为通过。

## 八、下一会话启动提示词（复制即用）

> 继续 agent 仓库 eval-closure 接手批。先读 docs/tasks/handoff-batches/NEXT-SESSION-2.md
> （本轮 E 批代码已完成未提交，含设计事实/踩坑/收尾步骤）与 docs/tasks/eval-closure/TASK.md，
> fetch 核对 main 最新并 ls docs/tasks/ 防撞车。然后按序执行：
> ① E' 收尾——到 worktree agent-wt-audit-r1（feat/eval-closure-backend 分支）核对 8 个
> 实现文件 + 本交接稿与 41 passed 当前基线（原始 40 条已复跑），向用户展示 Diff 求确认后 commit/push/PR/CI 全绿/合并/主仓 pull/
> devctl rebuild/beat 注册确认，并开 EVAL_ONLINE_SAMPLER_ENABLED 实机验证一次
> trigger='online' 落库后还原；② D1 dev-only 124 包复检（文档批）；③ F 批前端三项
> （前置已满足，开工前逐文件 diff 核对三 tab 基线）；④ D3/D4a 依赖升级（八步链）。
> 每批独立分支独立 PR；ECS 三项不处理；pillow/filelock/dnspython/unstructured 挂起勿翻案；
> 推送/PR 用 HTTPS_PROXY=http://127.0.0.1:7897；全程中文汇报，未执行的验证不得报通过。
