# 接手计划与收尾记录（NEXT-SESSION-2，2026-11-05 更新）

> 取代 `NEXT-SESSION.md`（其环境、方法教训和挂起裁决仍有效）。截至本次核对，
> E′、D1、F、D3、D4a 均已完成并合入 main；没有剩余的计划内实现批次。
> F 的真实浏览器 + APISIX JWT 写入链路仍未验收；在线采样长期启用前的后台预算
> 预占/结算风险仍未关闭。详细证据见 `docs/tasks/eval-closure/PROGRESS.md`。
> **用户已裁决（继承）：ECS 三项不处理；pillow/filelock/dnspython/unstructured
> 因 py3.11 不可达保持挂起勿翻案。**

## 一、已知信息（本轮新增，免重探）

### E 批已完成基线（worktree 未提交状态，见「六、工作树」）

| 项 | 文件 | 内容 |
|---|---|---|
| B1 | `backend/evaluation/release_gate.py` | `DEFAULT_THRESHOLDS` 加 `p95_latency_ms=30000 / cost_per_case_cny=1.0 / degradation_rate=0.05`（全部 direction=lower）；三项进 `LOWER_IS_BETTER`/`GATE_METRIC_LABELS`/`METRIC_SOURCE`；进 **RAG 与 SQL policy 的 optional**（有值裁决超线 FAIL、缺失 N/A 不影响存量 gate——required 会把所有现存 suite 干成 FAIL，勿改成 required） |
| B2 | `backend/evaluation/online_sampler.py`（新建） | `run_online_sampling()`：trace_store 权威源采样 → judge 打分 → `ai.eval_run_records`（trigger='online'）+ `ai.eval_run_samples`（evaluator='judge'）落库 |
| B3 | `backend/tasks/eval_sampling_tasks.py`（新建）+ `celery_app.py` include/beat_schedule + `queue_router.py` 登记 | task `evaluation.online_sampling`，每日 01:30 UTC，queue=report（beat_queue 派生） |
| 测试 | `tests/evaluation/test_release_gate_module_aware.py`（+93 行 7 用例）、`tests/evaluation/test_online_sampler.py`（新 17 用例） | **48 passed**（采样/门禁 + rag_smoke workflow 契约）；路由/拓扑守卫此前 **48 passed** |

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
4. **trace 优先信号全部现成**：`status`（error/failed/cancelled/degraded/rejected/success）、
   `metadata.degraded`+`degraded_reasons`（降级）、feedback.vote='negative'。
   「低置信」无独立字段（rejection.scores 语义未核实），用 degraded 替代，如需补先核实。
5. **judge 契约**：`judge_answer(question, rubric, answer)` 永不抛（catch-all 返
   total=0.0）；0.0 → status='error' 计数不吞、不进均值（与 rag.py:843 先例一致）。
   及格线 judge_total≥3.0（60 分）→ pass。eval_gen 未绑定模型时全部 0.0——
   生产首次开 `EVAL_ONLINE_SAMPLER_ENABLED=true` 前先确认 eval_gen 已绑模型。
6. **答案预览边界**：`TraceRecord` 保存全文字符数 `answer_len`，但 trace_store 仅持久化
   `answer_preview=answer[:200]`；长度不匹配或缺字段时样本记 `skip`，记录
   `error_stage=answer_preview_incomplete` 和 `judge_skipped_count`，不对截断片段评分。
7. **幂等**：run_id 按天 `online-YYYYMMDD`；records ON CONFLICT 更新 run 摘要，
   样本明细在同一事务内先清理该 run 的旧 judge 行、再写当前行集，避免同日重跑
   后摘要计数与样本明细不一致。
8. **采样开关与参数（env，治理边界：不建配置页）**：
   `EVAL_ONLINE_SAMPLER_ENABLED`（默认 false）、`EVAL_ONLINE_SAMPLE_RATE`（0.05）、
   `EVAL_ONLINE_SAMPLE_MAX`（20）、`EVAL_ONLINE_SAMPLE_WINDOW_HOURS`（24）。
9. **B1 判定路径**：`_evaluate_requirement` optional+有值+阈值（DEFAULT 恒有）→
   判定；三项放 optional 是向后兼容唯一可行解，TASK.md 验收「缺数据 fail-closed」
   由 required 既有铁律覆盖（守卫用例 test_perf_optional_does_not_mask_required_fail）。

### 环境与操作纪律（全部继承 NEXT-SESSION.md 第一节）

代理（git push/gh 带 `HTTPS_PROXY=http://127.0.0.1:7897`）、devctl rebuild 用
`python -c subprocess` 包 cmd.exe（timeout ≥7000s）、PostgreSQL 显式 5433
（容器用户 postgres/agent_memory）、Mimosa hook 拦 Bash heredoc 写文件（走 Write/Edit）、
py3.10 铁律。**本轮新坑**：Bash 里 `sed -n` 纯读取 + grep 组合也可能被 hook 误判为
写源码——读文件优先用 Read 工具，grep 单独发。

## 二、总计划与当前状态

| # | 内容 | 状态 | 前置 |
|---|---|---|---|
| E′ | 后端门禁指标、在线采样器和 Beat 调度 | **已完成并合并**（PR #326；实机 `trigger=online` 落库后开关还原） | 无 |
| D1 | dev-only 124 包语义复检 | **已完成**（PR #327；文档复检，未改 lock） | 无 |
| F | 管理端三项：RunsTab、DatasetsTab、CompareTab | **已完成并合并**（PR #328；隔离写操作验收记录 PR #331） | 无；浏览器 + APISIX JWT 联合链路仍 UNVERIFIED |
| D3 | LangChain/LangGraph 依赖族升级与复核 | **已完成**（版本已由供应链 PR #314 合入；PR #329 记录核验，无重复改 lock） | 无 |
| D4a | cryptography / datasets / oauthlib 跨大版本升级与复核 | **已完成**（版本已由供应链 PR #314 合入；PR #330 含测试兼容修正，无重复改 lock） | 无 |
| 收尾项 | F 浏览器 + APISIX JWT 实际写入验收（如需端到端验收） | **UNVERIFIED**；隔离 API 写操作已经通过，不等价于真实 JWT 验签和浏览器操作 | 安全的隔离验收环境与 super_admin 登录 |
| 风险项 | 在线采样长期启用前补齐后台预算预占/结算 | **未关闭**；当前 `EVAL_ONLINE_SAMPLER_ENABLED` 默认关闭 | 单独裁决后台任务预算方案 |

计划内批次已完成；表中的收尾项和风险项是剩余验收/运行边界，不代表对应验证已通过。

## 三、E' 收尾与实机验收（已完成）

- PR [#326](https://github.com/Jayus-07/agent/pull/326) 最终 HEAD `9bf0350b68ac5a0e2a10c681e5b577d046f448a2`，
  已于 2026-11-04 合并，merge commit `dd1565612823396a1550512b98432205efc98c96`。
- Review 修复后的后端/采样与 RAG workflow 定向套件 **48 passed**；PR 必需 checks 通过，
  其中真实 API 评测 job 按 workflow policy 标记 SKIPPED（不计作已执行通过）。
- 主仓已 fast-forward 到 `dd15656`；从任务 worktree 执行 `devctl.bat rebuild /y` 成功，
  app、worker、beat、admin/web 等服务 healthy；`8000/health`、`8081/health`、
  `9080/health` 与三个前端 `/login` 均返回 200。
- `beat` 实际注册 `evaluation.online_sampling`，queue=`report`，每天 09:30（Asia/Shanghai）。
- `agent-report-worker-1` 中临时启用采样开关并手动触发一次任务：run id
  `online-20261104-verify-20261104-215726`，结果 `persisted=true`、sampled=1、judge_failed=0、
  judge_skipped=0；查询 `ai.eval_run_records` 确认 `trigger=online`、
  `triggered_by=celery_beat`，样本行 `status=pass`。临时环境变量随子进程结束还原，
  worker/beat 与主 `.env` 均未留 `EVAL_ONLINE_SAMPLER_ENABLED`，默认仍关闭。
- **未关闭风险**：实机 judge 调用出现 `[Budget] reserve 旁路` 日志（后台任务无请求级预算上下文）。
  本次只验收一次性触发和落库；长期启用前须单独裁决并补齐后台预算预占/结算链路。

## 四、D1 步骤（E' 合并后可立即做，文档批）

1. 已核对 `requirements-lock.txt` 的第二段 124 个 dev-only pins；PyPI JSON 请求 124/124 成功，
   69 个最新版本高于锁定版，55 个已是最新。15 个最新版本要求 Python ≥3.11。
2. 官方 release/changelog 发现 FastMCP、cyclopts、smart_open、pypdfium2、pytest、watchdog
   的 breaking 变化；FastMCP/cyclopts/smart_open/pypdfium2 未发现直接调用，pytest 静态 AST 检查未发现受影响参数形态，
   依赖保持锁定，升级需单独评估。
   `thinc 10.0.0` 的上游说明称其代码等同 `8.3.14`；`openapi-pydantic 0.6.0` 只移除
   Python 3.8 支持，不影响当前 py3.10 基线。
3. `srsly 3.0.1` 的官方 release note 是 bug-fix；但锁定的 spaCy 3.8.14 要求
   `srsly<3.0.0`，禁止单独升级，继续保持 `2.5.3`。结论及官方链接已记入
   `docs/tasks/audit-remediation-r1/PROGRESS.md`；本批未修改依赖。

## 五、F 批结果与验收边界（已完成）

- PR [#328](https://github.com/Jayus-07/agent/pull/328) 已合并，merge commit
  `83aa74ca81a2c91774d084dfd41e80d059fe90a2`。RunsTab 接 Gate 状态/审批/online
  徽标；DatasetsTab 接反馈候选审核与晋升；CompareTab 挂载人工评审并标注不进入自动门禁。
- 组件测试 4 个文件 / 6 个测试通过，`tsc --noEmit` 通过；任务 worktree 重建及 API /
  管理端健康检查通过。PR 合并时真实 super_admin 只读 API GET 与 logout 返回 200。
- PR [#331](https://github.com/Jayus-07/agent/pull/331) 合入隔离 API 写操作验收：F1 审批
  GET 200 → POST 201 → 读回 200；F2 approve/reject/promote 均 200 且读回状态正确；
  F3 人工评审 GET 200 → POST 201 → 读回 200。隔离数据库和临时 JSONL 已清理。
- **未验收**：上述 F 写操作使用测试 API key 和模拟网关注入的 super_admin 身份头，
  未经过 APISIX JWT 验签，也未通过真实管理端浏览器点击；不能将联合链路标记为通过。

## 六、工作树

- 本次核对时主仓 `main` 与 `origin/main` 均为 `dc4a181`（PR #331），主仓仅有其他会话
  的未跟踪目录；均未触碰：`docs/tasks/.mimosa/`、`docs/tasks/trace-probe-isolation/`、
  `docs/tasks/trace-query-pushdown/`、`docs/tasks/trace-ux-pack/`、`frontend/src/.mimosa/`。
- 已合并批次的工作树仍保留且核对时干净：`agent-wt-audit-r1`（PR #328 分支，HEAD
  `2732047`）、`agent-wt-eval-d3`（PR #329，HEAD `25aa44f`）、`agent-wt-eval-d4a`
  （PR #330，HEAD `8abde7c`）、`agent-wt-eval-f-acceptance`（PR #331，HEAD `6808bdb`）。
  未清理这些工作树，也未触碰其他会话工作树。
- 后续如开展新的实现，仍需先 `git fetch`、核对 `docs/tasks/` 和工作树状态，并从当时
  最新 `origin/main` 建独立分支；本计划中的 F/D3/D4a 不要重复开工。

## 七、测试与验收（继承八步链 + E 批专项）

- 依赖批（D3/D4a）八步链已由供应链 PR #314 的 lock 升级证据覆盖：① requires_python
  逐包 → ② OSV 检查 → ③ 更新 lock → ④ dry-run 全量解析 → ⑤ PR CI → ⑥ 合并 →
  ⑦ pull+rebuild → ⑧ 容器 freeze 一致性、导入冒烟和 `/health`。D3/D4a 的目标包复核及
  定向测试分别见 `docs/tasks/eval-closure/PROGRESS.md`；D3 **41 passed**，D4a **76 passed**。
- E 批验收（TASK.md）：B1 三态裁决+缺数据 fail-closed（Review 修复后采样/门禁与
  rag_smoke workflow 契约 48 passed）；B2/B3 beat 已实机触发且 `trigger='online'` 落库；
  rebuild/health/beat 注册已核验。长期启用前后台预算链路风险仍需裁决，详见第三节。
- F 批验收：三 tab 各自 API 实际调用成功（super_admin gate 审批闭环、候选
  approve/reject/promote、人工评审提交）+ tsc/组件测试绿；真实浏览器 + APISIX JWT
  联合写入链路仍 **UNVERIFIED**，详见第五节及 `eval-closure/PROGRESS.md`。
- 未实际执行的验证不得报告为通过。

## 八、下一会话启动提示词（复制即用）

> eval-closure 与本计划的 E′、D1、F、D3、D4a 均已完成并合入，不要重复实施。若继续做
> 端到端验收，只在隔离数据上验证真实浏览器 + APISIX JWT 的 F 写操作链路，并如实记录结果；
> 在线采样保持默认关闭，长期启用前先解决后台预算预占/结算旁路。ECS 三项不处理；
> pillow/filelock/dnspython/unstructured 继续挂起，勿翻案。新代码任务仍需从最新 main
> 开独立 worktree/PR，推送使用 HTTPS_PROXY=http://127.0.0.1:7897，未执行的验证不得报通过。
