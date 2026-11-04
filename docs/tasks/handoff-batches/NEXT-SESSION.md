# 剩余批次接手计划（NEXT-SESSION，2026-11-04 收尾会话写）

> 覆盖 audit-remediation-r1 衍生的依赖升级第二波 + eval-closure 实施。
> 恢复任务时先核对：main 最新 SHA、`ls docs/tasks/` 防撞车、本文件与
> `docs/tasks/audit-remediation-r1/PROGRESS.md`（七轮+P3 全程证据链）。
> **用户已裁决：ECS 三项核实本次不处理**（保持 BLOCKED，等用户恢复访问后再立项）。

## 一、已知信息（免重探，接手必读）

### 环境与操作纪律
- 仓库 `Jayus-07/agent`；主仓 `D:\Program Files\workplace\agent` 只做 pull/重建，
  改代码一律走独立 worktree；开工前先读 `.zcode/skills/agent-repo-pitfalls`。
- 网络：直连 GitHub 失败，git push / gh 命令全部带
  `HTTPS_PROXY=http://127.0.0.1:7897 HTTP_PROXY=http://127.0.0.1:7897`（命令级，不改全局）；
  代理偶发抖动（SSL EOF / IncompleteRead），重试即可。
- devctl rebuild：Git Bash 里 `cmd /c` 会被 MSYS 路径转换吃掉，用
  `python -c "import subprocess; subprocess.run(['cmd.exe','/c','devctl.bat','rebuild','/y'], ...)"`；
  `/y` 跳交互确认；依赖层全量重装约 40 分钟，subprocess timeout 给 ≥7000s。
- Mimosa PreToolUse hook 会拦：Bash heredoc 写文件、Bash 直接写 requirements-lock.txt
  （一律走 Write/Edit 工具）；临时脚本含 urllib 请求时要写显式
  `scheme=='https' and hostname=='白名单'` 断言（SSRF 检查口径），否则拒写入。
- PostgreSQL：agent 项目库一律显式 5433（宿主机 5432 是另一个同名库）。
- **Python 版本铁律**：本地 D:/Python = 3.10.2、镜像 python:3.10-slim、CI = 3.10
  ——三处一致。任何目标版本 `requires_python >=3.11` 在当前环境**装不上**，
  升级前必须先查（本轮已因此挂起 dnspython/unstructured/filelock/pillow，见下）。

### 本轮已完成基线（勿重做）
- audit-remediation-r1 P3 全部合并：PR #304（A1 依赖分诊+A2 SQL 注入收口）、
  #305（B1-B4）、#306（C 生产配置清单）、#308（travel 17 处租户 fail-closed）、
  #310（依赖升级批 1：24 包+连带 yarl）、#311（A1 方法学修正）、
  #314（供应链收口：Dockerfile 消费 lock）、#316（批次 1 合并后核验）。
- **requirements-lock.txt = 368 包并集**：生产容器冻结 244 包（权威运行时，
  含 torch==2.14.1+cpu）+ 老锁 dev-only 124 包（CI pytest 等依赖，原 pin）。
- **Dockerfile 三步安装**：torch CPU wheel（constraints/torch-cpu.txt）→
  `pip install -r requirements-lock.txt`（+CPU 索引）→ `pip install --no-deps -e .`。
  pyproject 不再参与构建期解析——**改 lock 即改生产依赖**。
- 7 个 workflow 装 lock 均已带 `--extra-index-url https://download.pytorch.org/whl/cpu`
  （+cpu 轮子不在 PyPI）：frontend-admin / evaluation-v2 / rag_smoke（2 处）/
  tool_quality / full_regression / prompt_eval / rag_regression。
  **新增装 lock 的 workflow 必须同样带索引**。
- 依赖升级标准流程（lock 头部已写）：改 lock → `pip install --dry-run` 全量解析
  （剔除 torch 行后本地验证；URL 直连依赖 zh_core_web_sm 已确认无消费方并移除）
  → PR CI（真实安装）→ 合并 → rebuild → 容器 `pip freeze` 与 lock 一致性核验
  （批次 1 实测 368/368 零漂移）→ 导入冒烟 → `/health` 于 :8000/:9080 双 200。
- travel 租户收口已完成：`_travel_tenant` 助手存在，全文件 `or "default"` 清零。
- 依赖公告事实源：`docs/tasks/audit-remediation-r1/evidence/osv_dedup_result.json`
  （43 包 / 287 条独立公告；逐包结论见同目录 PROGRESS「P3 批次 A」）。
- ECS 三项（GATEWAY_SESSION_CHECK / JWT_SESSION_GUARD_MODE / 9080）：**BLOCKED**
  （主机密钥变更 + publickey 对 root/ubuntu×id_ed25519/agent 四组合全 403，
  判定重装后 authorized_keys 未恢复；两条恢复路径已写入
  `docs/operations/production-config-checklist.md`）。**本次不做**。

### 方法教训（防再犯）
1. 「可升级」结论必须**先对目标版本跑 OSV 清零验证**（pillow 12.3.0 升完仍命中
   同批 54 条；13.0.0 才 0 命中——只验"比现版本新"不够）。
2. 目标版本必须查 PyPI `requires_python`（py3.10 铁律）。
3. pip dry-run 冲突是迭代暴露的：yarl 被 aiohttp 拉、fsspec 被 datasets 钳、
   langchain 族互钳——逐轮消解，不硬闯。
4. langchain/langgraph 是**整族联动**：动一个必须同步评估 langchain-core /
   langgraph / langgraph-sdk / langgraph-checkpoint(-postgres) 全部。
5. otel 家族 api/sdk/exporters（1.45.1）与 instrumentation（0.66b1）必须同世代
   （容器实况即此组合；CI 首轮曾因混入老锁 0.62b1 全线挂）。

### 并行会话（防撞车）
- `eval-center-simplify`：worktree `agent-eval-simplify`，Phase D 执行中，正在改
  `frontend-admin` EvaluationCenter 三组件（RunsTab/DatasetsTab/CompareTab）；
  其冻结区恰好 = 本计划 F 批内容。**F 批开工前必须 `git fetch` 确认其已合并进
  main；未合并则只做 E 批**。
- 开工前 `ls docs/tasks/` 核对有没有新任务档案与本计划撞车。

## 二、总计划（剩余批次，按优先级排序）

| 批次 | 内容 | 风险 | 前置 | 建议分支 |
|---|---|---|---|---|
| E | eval-closure 后端三项（表现阈值门禁/在线采样/Beat 调度） | 中 | 无，**可立即开工** | `feat/eval-closure-backend` |
| D1 | dev-only 124 包语义复检（轻量，产出结论文档） | 低 | 无 | `docs/deps-d1-devonly-review` |
| F | eval-closure 前端三项（纯接线） | 低 | eval-center-simplify 已合并 | `feat/eval-closure-admin-ui` |
| D3 | langchain/langgraph 整族升级 | 中高 | 独立批 | `fix/deps-d3-langchain-family` |
| D4a | 跨大版本可装三项：cryptography 50 / datasets 5.0.1 / oauthlib 4.0 | 高 | 独立批+定向回归 | `fix/deps-d4a-cross-major` |
| 挂起 | 见下「明确不做/挂起」 | — | — | — |

（D2 pillow 已改判挂起，见「挂起」节；原 D2 撤销。）

### E：eval-closure 后端（细节见 `docs/tasks/eval-closure/TASK.md`，已入库）
- B1 `backend/evaluation/release_gate.py`：`DEFAULT_THRESHOLDS`（约 :72-92）加
  P95 时延/单例成本 CNY/降级率，复用现有 required/optional 三态机制，不加新判定分支；
- B2 `backend/evaluation/online_sampler.py` 新建单文件：按比例抽近期 trace
  （优先负反馈/低置信/失败终态），复用 `evaluation/judge.py` 打分，写现有
  `ai.eval_run_records` + `ai.eval_run_samples`（迁移 056/075，零新表），`source=online`；
- B3 Celery Beat 注册每日跑 B2，采样率读环境变量；
- 测试：gate 三态 fail-closed 用例 + sampler 采样口径/judge 失败不吞/落库字段。

### F：eval-closure 前端（前置硬性：eval-center-simplify 已合并）
- F1 RunsTab：gate 状态列（绑 `gate_verdict/gate_reason`，api/evaluation.ts:296-297）
  + super_admin 审批按钮（`getV2Gate/createV2GateApproval` :725-737）+ `online` 来源徽标；
- F2 DatasetsTab：feedback 候选过滤 + approve/reject/promote
  （`reviewFeedbackCandidate/promoteFeedbackCandidate`，api/feedbackCandidates.ts:16-21）；
- F3 CompareTab：用例详情挂 `EvaluationHumanReviewButton` + 「不进自动门禁」标注；
- 全部为挂接已存在 API/组件，不新增页面/权限面；tsc + 组件测试绿。

### D1：dev-only 124 包语义复检
- 现状：CI 全绿证明"能装 + 现有测试过"；复检目标是识别"老 pin 是否有影响测试
  语义的过时项"。轻量做法：对 124 包 diff 最新版本，仅逐个评估"有新版且
  changelog 含 breaking"的条目；无实际问题的在 PROGRESS 记「复检通过，无需变更」。
- 产出：复检结论表（文档批即可）；发现真问题才改代码。

### D3：langchain/langgraph 整族
- 目标组合：langchain 1.4.6 + langchain-core ≥1.6.10 + langgraph ≥1.2.12 +
  langgraph-sdk 0.4.4 + langgraph-checkpoint-postgres 3.1.3（连带 checkpoint 高版本，
  老锁注释「4.0.3 与 checkpoint-postgres 不兼容，已实测 4.2.0」可参考）；
  langsmith 0.8.18 已在批 1 升过不用动；注意锁内 `langchain-classic==1.0.7` 与
  `langchain-classic` 的版本契约，dry-run 会暴露。
- 定向回归面：RAG 链路相关测试、checkpoint（test_pg_saver_pool /
  test_task_checkpoint_recovery）、graph/orchestration 相关。

### D4a：跨大版本可装三项
- 已验证兼容：cryptography 50.0.0（rp >=3.9，!=3.9.0/3.9.1）、datasets 5.0.1
  （rp >=3.10.0）、oauthlib 4.0.0（rp >=3.9）。
- 风险点：cryptography 影响 Authlib/JWT/签名链路（定向：test_authn_hardening、
  test_auth_local、session_family）；datasets 影响 RAG 数据集加载（定向：RAG
  datasets/evaluation 相关测试面）。三项若 dry-run/回归中发现强耦合可再拆分。
- setuptools 84 虽兼容（>=3.10）但维持「随基础镜像」既定裁决，不进本批。

## 三、明确不做 / 挂起（防翻案）

| 项 | 状态 | 理由 |
|---|---|---|
| ECS 三项核实 | **BLOCKED，用户已裁决本次不处理** | 等用户恢复 SSH 访问后另立项 |
| pillow 清零（13.0.0） | **挂起** | **13.0.0 requires_python >=3.11（已实测），py3.10 全环境不可达**；前置 = Python 3.11 升级专项（大工程，未立项，不得擅自启动） |
| filelock 4.2.1 | **挂起** | rp >=3.11（已实测），同上 |
| dnspython 2.9.0 / unstructured 0.24.1 | 挂起 | rp >=3.11（批 1 已实测） |
| D 批次 competitor 租户列 | 默认不实施 | 单租户现实；仅用户明确启用多租户才立项 |
| 通用灰度发布/回滚框架 | 冻结 | 仅 CS 域特例存在；属流程扩张 |
| nltk/PyJWT/chromadb/transformers 各 1 条公告 | 挂起 | 上游无修复版本，盯上游 |
| pywin32 PYSEC-2021-112 | 豁免（疑似误报） | fix=301 < 现版本 312；可顺手复核原文后转正 |
| setuptools/pip | 延期 | 随基础镜像 |
| opentelemetry 单跳 api | 延期 | 家族必须同世代协同升 |

## 四、工作树

- 依赖批与 E 批可复用 `D:\Program Files\workplace\agent-wt-audit-r1`
  （本轮全部分支已合并、工作区干净、.env 在位）或按需新建；
  `agent-wt-audit-p3b` 已清理。
- 每批次独立分支（命名见总计划表），从最新 `origin/main` 建；
  完成一批合并一批，不让多批混在一个分支。
- 不动其他会话 worktree（agent-eval-simplify、agent-gov、agent-cs-*、
  agent-wt-travel-* 等）；新建 worktree 记得从主仓拷 `.env`。

## 五、测试与验证约定

- 定向跑受影响面，不跑全量（testing-guide T0–T2）；不以扩大 mock/skip 掩盖失败。
- 依赖批次固定验证链（八步，缺一不可）：
  ① PyPI requires_python 逐包校验 → ② 目标版本 OSV 清零验证 →
  ③ 改 lock → ④ `pip install --dry-run` 全量解析（剔 torch 行）零 ERROR →
  ⑤ PR CI 全绿（含 7 个装 lock workflow 的真实安装）→ ⑥ 合并 →
  ⑦ 主仓 pull + `devctl.bat rebuild /y` → ⑧ 容器 `pip freeze` 与 lock 一致性
  + 导入冒烟 + `/health` 双 200。
- E/F 批验证见 `docs/tasks/eval-closure/TASK.md` 测试节；
  F 批前端改完必须 tsc + 组件测试 + rebuild 后真实页面走查。
- 未实际执行的验证不得报告为通过。

## 六、验收标准

- 每批次：独立 PR → CI 全绿 → 合并 → 主仓 pull → rebuild → 健康确认（八步链）。
- E 批：release_gate 对含表现指标 suite 出三态裁决且阈值缺失 fail-closed；
  beat 触发一次采样 run 落库且 `source=online` 可过滤。
- F 批：super_admin 在 RunsTab 完成 gate 审批闭环、DatasetsTab 完成
  approve/reject/promote、CompareTab 可提交人工评审（均 API 实际调用成功）。
- D1：复检结论表落档（大概率是文档批）。
- D3/D4a：升级后容器 freeze 与 lock 零漂移 + 对应定向回归绿 + 导入冒烟。
- 全程中文汇报；合并走 PR（含直推场景自开自合）。

## 七、下一会话启动提示词（复制即用）

> 继续 agent 仓库剩余批次。先读 docs/tasks/handoff-batches/NEXT-SESSION.md
> （接手计划，含已知信息/教训/防撞车）与 docs/tasks/eval-closure/TASK.md，
> fetch 核对 main 最新并 ls docs/tasks/ 防与并行会话撞车，确认
> eval-center-simplify 是否已合并。然后按优先级实施：E 批（eval-closure 后端
> 三项，可立即开工）→ D1（dev-only 复检）→ F 批（仅当 eval-center-simplify
> 已合并）→ D3/D4a（依赖升级第二波）。每批次独立分支、独立 PR、八步验证链；
> ECS 三项明确不处理；pillow/filelock/dnspython/unstructured 因 py3.11 不可达
> 保持挂起勿翻案。工作树复用 agent-wt-audit-r1，推送/PR 用
> HTTPS_PROXY=http://127.0.0.1:7897。全程中文汇报，未执行的验证不得报通过。
