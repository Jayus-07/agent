# 接手计划与收尾记录（NEXT-SESSION-2，2026-11-05 更新）

> 取代 `NEXT-SESSION.md`（其环境、方法教训和挂起裁决仍有效）。截至本次核对，
> E′、D1、F、D3、D4a 均已完成并合入 main；没有剩余的计划内实现批次。
> PR #333 已合并，当前 `main`/`origin/main=a479949`；最终 HEAD `79aec108` 的必需 CI 成功，真实 API job 按策略 skipped。合入代码的本机 rebuild、Beat 注册和全栈健康已核验。
> F 的真实浏览器 + APISIX JWT 拒绝写入与隔离 DB 读回已于 2026-11-05 完成；Sampler 仍默认关闭，修复后的真实预算账本请求未再触发。详细证据见 `docs/tasks/eval-closure/PROGRESS.md`。
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
| F | 管理端三项：RunsTab、DatasetsTab、CompareTab | **已完成并合并**（PR #328；隔离写操作验收 PR #331；浏览器 + APISIX JWT 另于 2026-11-05 实测） | 无 |
| D3 | LangChain/LangGraph 依赖族升级与复核 | **已完成**（版本已由供应链 PR #314 合入；PR #329 记录核验，无重复改 lock） | 无 |
| D4a | cryptography / datasets / oauthlib 跨大版本升级与复核 | **已完成**（版本已由供应链 PR #314 合入；PR #330 含测试兼容修正，无重复改 lock） | 无 |
| 收尾项 | F 浏览器 + APISIX JWT 实际写入验收 | **已完成**；真实 Edge 管理端拒绝候选，APISIX JWT access log HTTP 200，隔离业务库读回 `rejected` | 无 |
| 风险项 | 在线采样长期启用前补齐后台预算预占/结算 | **未关闭**；当前 `EVAL_ONLINE_SAMPLER_ENABLED` 默认关闭 | 单独裁决后台任务预算方案 |

计划内批次与浏览器收尾已完成；仅保留在线采样长期启用前的真实预算账本核验风险。

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
- 原实机触发曾出现 `[Budget] reserve 旁路`。PR #333 已在后台 judge 调用绑定 `scoped_request_budget`，并有定向测试与 CI 证据；修复后未进行真实模型请求验证预算 reserve/settle 账本。采样保持默认关闭，未来启用前须单独完成真实账本核验。
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
- PR #331 中记录的写操作仍是 API/测试密钥路径，单独看它们不证明 APISIX JWT 或浏览器行为；后续真实联合链路结果见本文第十节，已补齐此前缺口。

## 六、工作树

- 本次收尾时主仓 `main` 与 `origin/main` 均为 `a479949`（含 PR #333），主仓仅有其他会话
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
  rebuild/health/beat 注册已核验。PR #333 已补后台预算上下文并通过定向测试/CI；修复后的真实 reserve/settle 账本未实测，采样保持默认关闭，详见第三节。
- F 批验收：三 tab 各自 API 实际调用成功（super_admin gate 审批闭环、候选
  approve/reject/promote、人工评审提交）+ tsc/组件测试绿；真实浏览器 + APISIX JWT
  联合拒绝写入及隔离库读回已通过，详见第五、十节及 `eval-closure/PROGRESS.md`。
- 未实际执行的验证不得报告为通过。

## 八、下一会话启动提示词（复制即用）

> eval-closure 与本计划的 E′、D1、F、D3、D4a 和 F 浏览器/APISIX 收尾均已完成，不要重复实施。
> 在线采样保持默认关闭；未来如需启用，先单独核实 PR #333 修复后的真实预算 reserve/settle 账本，再改开关。
> ECS 三项不处理；
> pillow/filelock/dnspython/unstructured 继续挂起，勿翻案。新代码任务仍需从最新 main
> 开独立 worktree/PR，推送使用 HTTPS_PROXY=http://127.0.0.1:7897，未执行的验证不得报通过。

## 九、2026-11-05 续工状态

- 在线采样预算修复：`agent-wt-eval-sampler-budget` / `codex/eval-online-sampler-budget`。PR #333 初始提交 `533e02c` 的 19 个 sampler 定向测试通过；Codex Review P2 指出日固定 `run_id` 不应复用作预算 request ID。后续提交 `6acaea5` 已分离每次执行的预算 request ID；新增回归用例 **1 passed（43.77s，退出码 0）**，ruff check、py_compile、`git diff --check` 通过。修复后未重跑整个测试文件；pytest 会话结束时迁移残留守卫未能取得 DB 连接，SQL 未执行。
- PR #333 最终 HEAD `79aec108`：secret scan、Evaluation V2 Contract、RAG PR Smoke 成功；真实 API job 按 workflow 策略 skipped。PR 已合并，main=`a479949`；Sampler 保持默认关闭。
- Docker Engine 已恢复；从 `agent-wt-eval-sampler-budget` 执行 `devctl.bat rebuild /y`、`devctl.bat start all /y` 均退出 0。API/APISIX/business-mock 与三个前端入口 HTTP 200；Beat、report-worker、agent-worker、Postgres、Redis、Redis broker 健康。Beat 实际注册 `evaluation.online_sampling`、09:30 Asia/Shanghai、queue=`report`；环境未设置采样开关。本轮没有真实模型调用。整栈运行后空闲内存约 1.24 GB / 15.84 GB。
- F 联合验收完成：真实 Edge 管理端通过隔离 APISIX 提交 `e2e-closure-f2-20261105` 拒绝请求；APISIX 记录 `auth_type=jwt`、POST HTTP 200，隔离 `agent_business.public.feedback_candidates` 读回 `rejected`。隔离 fixture 原密码哈希已恢复、本次 session 已撤销，临时文件和剪贴板已清理；五个隔离容器已停止保留。
- 其他原批次已完成。ECS 三项不处理；pillow/filelock/dnspython/unstructured 继续挂起勿翻案。

## 十、2026-11-05 最终验收与 Docker 恢复

- Docker Desktop `29.2.1` 当前 Engine 可用。此前将锁定的旧 `dockerInference` IPC 目录移动到带时间戳备份后重启 Desktop，未做 factory reset、未删卷。`Not a git repo, skipping` 是 2026-10-24 安全审计中的历史记录，与 Docker 启动无关。
- 浏览器使用隔离管理端 `127.0.0.1:13201`，经隔离 APISIX `127.0.0.1:19081` 拒绝唯一 pending fixture `e2e-closure-f2-20261105`。网关 access log：`POST /feedback/candidates/e2e-closure-f2-20261105/reject`，`auth_type=jwt`、HTTP 200、user_id=`1`、trace_id=`2026-11-05 05:04:51-58-a140`；`agent_business.public.feedback_candidates` 读回 `status=rejected`、reviewer=`user:1`。没有写共享业务库。
- 为完成本机登录，临时更改隔离用户 `evale2e` 的密码哈希；验收后恢复原哈希、撤销本次 session、删除临时备份文件并清空剪贴板。隔离 Postgres/Redis/API/APISIX/admin 容器停止保留，未删除容器或卷。
- 从已合并代码工作树 `agent-wt-eval-sampler-budget`（HEAD `79aec108`）运行 `devctl.bat rebuild /y` 与 `devctl.bat start all /y` 均退出 0。`8000/health`、`9080/health`、`8081/health`、`3100/3200/3300/login` 全部 HTTP 200；app、workers、beat、Postgres、Redis、Redis broker、business-mock 和前端容器健康。此前 Redis broker 保持 stopped，故单独启动既有 `agent-redis-broker` 后 report-worker / agent-worker 恢复 healthy；未改其配置。
- Beat 容器实际注册 `evaluation.online_sampling`，schedule=`09:30` Asia/Shanghai、queue=`report`；report-worker 注册清单含该任务。`EVAL_ONLINE_SAMPLER_ENABLED` 未出现在 Beat/report-worker 环境中，默认 false。本轮未触发采样任务、未发真实模型请求。最终主机空闲内存约 1.24 GB / 15.84 GB；重建期间观测最低约 1.68 GB，未发生 OOM。
- **剩余运行风险**：PR #333 已补后台预算上下文，并有定向回归与 CI 证据；修复后未再使用真实模型请求验证预算 reserve/settle 账本。在线采样继续关闭，未来启用前单独完成该验证。
