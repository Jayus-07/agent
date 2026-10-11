# CI / DB 模型 / 向量库施工 GPT 审查包（2026-10-11）

> 第 3 阶段施工：CI / 自动评测门禁修复 + DB API 模型口径切换 + 隔离向量库。
> 本报告为随 PR 提交的独立审查材料。核心评测证据对应 `53810de0`；第二轮整改（GPT 复核回应）对应 `93e3dc7`，见 §10。

---

## 1. GitHub 信息

| 项 | 值 |
|---|---|
| PR | 见 PR 链接（task/ci-eval-gate-20261011 → main） |
| base SHA | `69ccde58fe95785f492c6f2e80bfcf559426125b`（origin/main） |
| HEAD SHA | `53810de01b9acaac82a643c2acbf5434f2458485` |
| 提交 | `e5ec1c7`（gate 双修复+回归测试）、`53810de`（CI 口径切换+seed 扩展） |

**修改文件清单**：

| 文件 | 目的 |
|---|---|
| `backend/evaluation/release_gate.py` | 修复 pass_rate 键错位（gate 恒 FAIL 的机制缺陷 A） |
| `backend/evaluation/gate/evaluator.py` | 修复 GATE-12 样本量每层死锁（机制缺陷 B） |
| `tests/evaluation/test_release_gate_tier_samples.py` | 新增 5 项回归测试 |
| `.github/workflows/rag_regression.yml` | 重写：DB API 模型口径 + 隔离库 + 评测前校验 |
| `.github/workflows/rag_smoke.yml` | 同上 + 路径检测补齐 MCP RAG/模型绑定面 |
| `backend/scripts/seed_prompt_eval_db.py` | 扩展可选 rerank 角色绑定；embedding 维度 env 化 |
| `scripts/seed_eval_model_bindings.py` | 新增：评测前绑定校验 + 真实 API 探针（--verify/--probe） |

**改动目的与影响范围**：仅触碰评测门禁与 CI 工作流；不改 RAG 检索算法、Embedding/Reranker 客户端、Prompt、业务链路、生产超时。

---

## 2. 旧 CI 失败原因与新 CI 设计差异

### 2.1 旧失败根因（14 连败实证）

RAG Regression 自 2026-10-01 起 14 次 run 全部失败，**无一次成功**。三层根因：

| 层 | 根因 | 证据 |
|---|---|---|
| 历史（已修） | suite `cases_hash` 与 canonical 行尾不一致 → 加载阶段炸（白跑 4 分钟） | `backend/evaluation/preflight.py` 模块 docstring 明文记录（10-01~10-05 五连败），preflight 于 10-06 落地 |
| 机制 bug A | `release_gate.compute_module_release_gate` 从 `ModuleSummary.metrics` 取 `pass_rate`，但该键只在顶层字段（`aggregate_metrics` 只聚合 per-case 指标）→ required pass_rate 恒 `missing_required` → **无论质量如何 verdict 恒 FAIL**，报告失真为「指标缺失」 | run 38086882164 报告 §1：「通过率 = 缺失/非有限值……value=None」，machine summary `"pass_rate": {"value": null}`，而同报告 §1 通过率 89.7% 真实存在 |
| 机制 bug B | suite 级 `min_samples=20` 被 `evaluate_tiers` 下放到每层；regression 套件 smoke(19)+hard(10) **单层永远 < 20** → GATE-12 样本量门数学死锁 | run 38086882164 报告：「[hard] 通过率 90.0% 有效样本量不足（GATE-12）」——hard 层通过率达标仍 FAIL |
| 口径层 | 旧 CI 用本地 bge-small-zh-v1.5（512 维）+ 本地 reranker 评测；生产是 DB 注册的 qwen3.7-text-embedding（1024 维）+ qwen3.7-text-rerank → **CI 口径系统性低估生产水位**（本地口径 smoke 89.5~94.7%，生产口径 100%） | 本轮隔离环境真实 API 评测：smoke 19/19=100%、exit 0（§5） |

失败 Workflow / Run 链接（示例）：
- https://github.com/Jayus-07/agent/actions/runs/38086882164（10-10，schedule，exit 1，失败步骤 "Run selected RAG regression"）
- 全部 14 次：workflow `rag_regression.yml` 历史页，最早 36934841000（10-01）

### 2.2 新旧设计差异

| 维度 | 旧 rag_regression/rag_smoke | 新（本 PR） |
|---|---|---|
| 模型口径 | ENV_MODE=local，BGE 本地模型，VECTOR_PG_DIM=512 | ENV_MODE=cloud，DB specialized binding 解析（qwen3.7 系列，1024 维），禁止本地回退 |
| 数据库 | service pgvector + init_db（但向量口径与生产脱节） | 同 pgvector 隔离库（agent_memory_test）+ **全套 DB env**（MEMORY/VECTOR/RAG_STORES/DOC_REGISTRY/OBS_DB/WORKFLOW_DB）|
| 模型绑定 | 无（评测无生成模型，[EvalGen] 恒 WARN） | seed_prompt_eval_db（secrets→加密入库）+ verify + **真实 API 探针** |
| 凭据 | 无 | GitHub Secrets（environment: prompt-eval 保护），运行时从 DB 加载，无 env 第二配置源 |
| 语料范围 | data/docs 全量（业务语料混入 sync） | Trim：删除 policy_general 等业务目录，只留评测语料 |
| 完整性 | 无断言 | 向量数/维度/KB 断言（Assert index completeness），空集合直接 BLOCKED |
| 退出码 | 直接命令（无管道） | `set -o pipefail` + `tee` + `exit $?`（原始退出码直捕） |
| 费用归因 | 无 | llm_usage 汇总导出 artifacts（隔离库账本，注明未同步生产计费） |
| 语义定位 | 名称含 Regression 但无基线对比说明 | workflow 注释明示：固定样本质量门禁，**不含**历史基线对比 |

---

## 3. DB 模型绑定验证证据（脱敏）

评测前两道校验（本地隔离库 `agent_memory_eval_ci` 与容器内均执行）：

```
[verify] embedding: model=qwen3.7-text-embedding provider=specialized-api adapter=dashscope_embedding dims=1024
[verify] rerank: model=qwen3.7-text-rerank provider=specialized-api adapter=dashscope_rerank dims=default
[verify] credentials: 可解密 fingerprint=cd68594a9cd9
[verify] EVAL_MODEL_BINDING_CHECK=passed

[probe] model=qwen3.7-text-embedding base_url=https://dashscope.aliyuncs.com/compatible-mode/v1
[probe] declared_dims=1024 actual_dims=1024
[probe] EVAL_MODEL_PROBE=passed
```

- 凭据只输出存在性与指纹（`cd68594a9cd9`），**明文/密文均未出现在任何日志**。
- 维度以 API 实际响应为准（probe 实测），不盲信配置声明。
- 校验失败即退出码 1（评测保持 BLOCKED，不切回本地模型）。

---

## 4. CI DB / 向量库 / BM25 隔离证据

- **数据库隔离**：`agent_memory_eval_ci` / `agent_business_eval_ci`（同实例独立 database，84 个迁移 init_db 建立），**不连接生产/共享 agent_memory**。
- **执行环境隔离**：一次性 Docker 容器（`agent-app-travel-v2-corefix:20261011` 镜像、`agent_agent-net` 网络、独立存储目录 `/app/backend/data` 每次清空），**不使用宿主 data/docs / DocumentRegistry / BM25**。
- **语料范围**：容器内 `data/docs` 仅保留 `rag_eval_kb`（部署目标）与 `rag_test_kb`（fixture 源）两个目录；业务知识库目录删除（防止 pipeline 全量 sync 混入业务文档与 near-dup 交叉）。
- **导入结果**：`documents: 16 | chunks: 169 | collection: rag_eval_kb | version: rag_eval_kb-baseline-2026-09-18`，`fixture import success`（IMPORT_EXIT=0），主语料 `active=0`（无业务语料混入）。
- **隔离断言**：导入后隔离库 `chunk_store=169`、`rag_vectors(collection=chroma, kb_id=rag_eval_kb)=169`，维度 1024..1024。
- 导入器的删除/`mark_deleted` 行为仅在隔离资源内执行（容器内 data/docs 已裁剪，sync delta 只见评测语料）。

---

## 5. 真实评测执行证据（受信任环境，本机隔离容器）

**命令与原始退出码**（无管道吞码，日志落盘）：

```
# 语料导入
python -m backend.evaluation.import_fixture baseline    → IMPORT_EXIT=0
# 评测
python -u -m backend.evaluation.cli rag --selection regression \
  --no-ragas --no-resume --triggered-by local-ci-fix-verify  → EVAL_EXIT_CODE=0
```

**结果**（run_id=`2026-10-11T12-55-53-46a24c`，评测集 regression 5.0.0-unified，29 用例）：

| 项 | 值 |
|---|---|
| 总体结论 | **PASS ✅ 可以发布**（verdict=PASS） |
| 通过/失败/错误 | 28 / 1 / 0，通过率 **96.5%** |
| 分层 | smoke 19/19=100%（阈 95%）✅；hard 9/10=90%（阈 70%）✅ |
| recall@5 / MRR / Top-1 | 0.8968 / 0.9048 / 0.8571（阈值 0.8/0.75/0.8）全部 ✅ |
| 拒答准确率 / 误答率 | 1.0000 / 0.0000 ✅ |
| P50 / P95 延迟 | 见报告 §12（answers.jsonl 逐条 duration_ms） |
| 失败反例 | RC-039「采购合同90000元的付款节点分别是什么？」期望 legal_采购合同.md，Top-1 命中 purchase_采购流程.docx（语义召回不足；该用例在 10-10 旧口径 CI 中同样失败，属真实检索水位案例，非本轮引入） |

**修复前后 gate 报告对比**：
- 修复前（run 38086882164）：`pass_rate.value=null` →「required 指标缺失」，tier `[hard] 有效样本量不足（GATE-12）`，verdict FAIL。
- 修复后（本轮）：`通过率 96.5% ≥ 0.95 ✅` 真实显示，tier smoke 100%/hard 90% 双达标，verdict **PASS**。

**门禁行为验证（单元测试，5/5 PASS）**：
```
pytest tests/evaluation/test_release_gate_tier_samples.py → 5 passed
- 正常达标 → PASS；真实值低于阈值 → FAIL 且原因带数值（不放水）；
- smoke(19)+hard(10)/min=20 → 样本量门不死锁；
- 总有效样本不足 → 各层 fail-closed 注明原因；
- 套件总量判定 + 分层通过率独立检查并存（一层无效样本计入 valid 扣减，
  不会被另一层数量掩盖——skip/error 均从 valid 扣除）。
既有回归：tests/evaluation/ 9 passed；backend gate 三件 62 passed。
```

---

## 6. API 实际调用与费用归因（P0-9，如实口径）

**核实结论：当前账本缺口 = UNVERIFIED。**

- 隔离库 `llm_usage`（Billing V2 账本表）：**0 行**——cloud embedding/rerank 走 langchain `OpenAIEmbeddings` / reranker 客户端**直调，不经 LLM proxy 记账出口**；`token_usage.jsonl`（tracker 旧路径）亦未落盘。
- 因此**不宣称**本次评测的精确 Token/费用已被系统账本记录，也不宣称已同步生产计费。
- 调用量估算（按请求次数与文本量，非账本值）：导入期 169 chunks 嵌入（批 10）≈ 17 次嵌入调用 + 导入关键词 LLM 增强 16 次（eval_gen→doubao）；评测期 29 用例 ×（query 嵌入 ~2 次 + rerank ~1 次 20 chunks）。按 DashScope 牌价量级估计单轮 < 0.1 元。**估算口径，非账本值。**
- 遗留登记（P1）：将评测链路 embedding/rerank 直调接入 Billing V2（price_usage/llm_usage），带 eval_run_id 归因。CI workflow 已实现 llm_usage 汇总导出步骤——账本有记录后即自动生效。

---

## 7. 事故披露（诚实口径）

施工过程中共享库 `agent_memory` 的**评测区**（`rag_eval_kb`）被触碰两次：

1. 第一次（宿主后台评测，连共享库）：`import_fixture` 停在「先删后写」的删除完成后 → rag_eval_kb 旧向量 254 行被删。
2. 第二次（容器 env 不完整，缺 VECTOR_PGDATABASE 等 → backend 默认库名回落 agent_memory）：两轮导入写入了共享库评测区（现 rag_eval_kb 向量 185 行 / registry 15 active）。

**损害面核查**：业务知识库无损——policy_general 28 active、travel 48 active、policy_hr 11、policy_finance 5、cs_* 各库 active 与历史量级吻合；`/health` healthy；业务向量 2053 行完好。被写入内容均为评测 fixtures 合法数据（1024 维生产口径）， rag_eval_kb 本就是评测专用库（生产检索不依赖）。

**已落实的防复发**：容器/workflow 补齐全套 DB env（6 项全指向隔离库）；workflow 语料裁剪步骤；评测前完整性断言。**恢复建议（待拍板）**：共享库 rag_eval_kb 现为新灌评测数据（可用）；如需恢复历史版本基线，用正确 env 重灌一次即可，另有 registry 中 1 行 pending_review（baseline_faq，第一轮 HOLD 残留）可清理。

---

## 8. GitHub Actions 真实结果

**最终收口（key 配置完成 + GPT 复核整改全部落地后）：**

| Run | 触发 | 结果 | 说明 |
| --- | --- | --- | --- |
| **[38154543463](https://github.com/Jayus-07/agent/actions/runs/38154543463)** | **workflow_dispatch（PR 分支 + regression suite，--strict-gate）** | **✅ SUCCESS（10m23s）** | **最终验收：strict-gate 端到端**——`EVAL_GATE_VERDICT=PASS` 完整 Gate 裁决输出 + probe ✓ + 索引断言 `vectors=169 dims=1024` ✓ + 96.5%（smoke 100%/hard 90%），CLI exit 0。修复过程：[38153508314](https://github.com/Jayus-07/agent/actions/runs/38153508314) 因参数行字面换行损坏 argparse 失败（7190316 修复） |
| **[38150689331](https://github.com/Jayus-07/agent/actions/runs/38150689331)** | **workflow_dispatch（PR 分支 + regression suite）** | **✅ SUCCESS（10m53s）** | **GitHub 上首次真实 API 评测全绿**：preflight ✓（29 用例/git_sha=4a6fd18/suite_hash=5976e7ea01fd621e）→ `EVAL_MODEL_BINDING_CHECK=passed` ✓ → `EVAL_MODEL_PROBE=passed` ✓ → 导入 169 chunks ✓ → `EVAL_INDEX_CHECK=passed vectors=169 dims=1024` ✓ → 评测 **96.5%（28/29），smoke 100%（19/19，阈 95%）、hard 90%（9/10，阈 70%）**，CLI exit 0（job 绿灯即分层门禁全过）。结果与本机隔离验证（§5）完全一致。Artifact：`rag-regression-38150689331` |
| [38150412116](https://github.com/Jayus-07/agent/actions/runs/38150412116) | pull_request | ✅ SUCCESS | PR 免费预检：路径检测 + E0 preflight + **19 项定向回归测试**（最终收口 1），无付费调用 |
| [38146905426](https://github.com/Jayus-07/agent/actions/runs/38146905426) | pull_request | ✅ SUCCESS（整改后语义） | 受信任入口收敛首轮实证：真实评测 job skipped |
| [38149897120](https://github.com/Jayus-07/agent/actions/runs/38149897120) | workflow_dispatch | ✗ BLOCKED | Key 未配齐时 Seed 显式 RuntimeError（过程记录；配齐后同链路即绿） |
| [38141724006](https://github.com/Jayus-07/agent/actions/runs/38141724006) | pull_request（旧策略） | ✗ init_db 连锁失败 | agent_business 预建缺失 → 已修（5337858） |

**CI 真实指标（run 38150689331 Artifact 实测，与本机报告分开记录）**：

| 指标 | CI 实测 | 本机隔离实测（§5） | 阈值 |
| --- | --- | --- | --- |
| 通过率 | 96.5%（28/29） | 96.5%（28/29） | ≥95%（smoke 层） |
| 召回率@5 | 0.8968 | 0.8968 | 0.8 |
| **MRR** | **0.8810** | 0.9048 | 0.75 |
| **Top-1 准确率** | **0.8095** | 0.8571 | 0.8 |
| 拒答准确率 / 误答率 | 1.0000 / 0.0000 | 1.0000 / 0.0000 | 0.85 / 0.1 |

两次运行的通过率与 recall@5 一致，MRR/Top-1 存在环境级微小差异（CI runner vs 本机 CPU 的检索排序波动，均达标）。失败反例同为 RC-039。

**strict-gate report.json 回放验证（零额外 API 费用，GPT 要求 6）**：CI run 38150689331 与本机隔离评测的两份真实 `report.json` 回放 `_strict_gate_exit_code` 均 **exit 0**（REPLAY-OK）；修复前的 P0 场景（分层达标但完整 Gate 不达标）由 `tests/evaluation/test_cli_strict_gate_exit.py` 5 项构造测试锁定。

**受信任执行入口证据（GPT 复核第 3 项）**：
- `gh api repos/Jayus-07/agent/environments/prompt-eval` 实测 `protection_rules: []`、`deployment_branch_policy: null`（整改前基线）——整改前同仓库未审核 PR 的 job 可直接获得付费 Secrets（run 38142127992 日志中 secret 值被 mask 即读到的实证）。
- 整改（不改用 `pull_request_target`）：`rag_smoke.yml` 拆分——`pull_request` 只运行免费预检 job（不引用任何 secrets），真实 API 评测 job 限 `push(main)` 与 `workflow_dispatch`；`rag_regression` 本就无 PR 触发（schedule + dispatch）。

**Environment 保护建议（最终收口 2，供仓库所有者在 Settings → Environments → prompt-eval 配置，施工方不代改）**：

1. **Required reviewers ≥ 1**（仓库所有者本人）：任何引用该 environment 的 job 启动前需人工批准，批准前 secrets 不注入——补强"有写权限者 dispatch ≠ 代码经审核"的缺口（批准动作即审核动作）。
2. **Deployment branch policy：restricted**，允许 `main` 与 `task/*`：阻止任意分支 job 使用该 environment。注意 pull_request 事件 job 的 ref（`refs/pull/N/merge`）不匹配策略会直接失败——与本设计不冲突（整改后 PR 事件只跑不引用 environment 的免费预检 job）。
3. 引用面审计（2026-10-11）：`rag_smoke.yml` 真实评测段（push main/dispatch）、`rag_regression.yml`（schedule/dispatch）、`prompt_eval.yml`（dispatch + repository_dispatch，后端 Prompt 发布回调，既有行为不在本轮改动范围）。
4. 保护规则配置后，正式执行方式固定为：**人工审核 PR HEAD → Actions → RAG Regression → Run workflow → 选定 PR 分支与 selection=regression → 批准 → 运行**。本次 run 38150689331 即按此方式执行（GPT 两轮复核通过的 PR HEAD `4a6fd18` + 仓库所有者 dispatch）。

---

## 9. P0 / P1 遗留清单（最终收口口径，P0/P1 不重复统计）

**P0（阻断合并）：0 项。**
原 P0-1「GitHub 真实评测 Run 未完成」**已闭环**：run [38150689331](https://github.com/Jayus-07/agent/actions/runs/38150689331) 在受信任入口真实跑通（96.5% PASS，§8）。

**P1（已批准另立任务，不阻断本 PR，不与 P0 重复计数）：**
1. **Billing V2 精确费用归因实施**（唯一归属地）：来源字段设计 + Embedding/Rerank 直调链接入账本 + eval_run_id 绑定。**本轮验收范围明确不含**（复核第 6 项批准另立）。当前事实：CI 隔离库 `llm_usage` 0 记录，workflow 已导出空账本汇总（`llm_usage_summary.json`，标注"未同步生产计费"）——**费用 UNVERIFIED，不视为零成本，也不作为本 PR 的合并阻断**。
2. 共享库 rag_eval_kb 恢复/清理拍板（§7）。
3. 历史基线对比（--compare/--promote-baseline）未启用——Regression 定位为固定样本门禁（§7）。
4. 版本化脱敏模型快照、已知样本检索探针、索引指纹缓存（[PR #31](https://github.com/Jayus-07/agent/pull/31) 规范中标注 ❌ 的契约项）。
5. RC-039（采购合同付款节点）语义召回不足——检索质量优化任务。
6. prompt-eval environment 保护规则配置（§8 建议 1/2，仓库所有者操作）。

**测试证据（本机 T1 + CI T1）**：
```
# 本机
python -m pytest tests/test_rag_smoke_path_filter.py \
  tests/evaluation/test_seed_eval_bindings_verify.py \
  tests/evaluation/test_release_gate_tier_samples.py
→ 19 passed（7 路径过滤正反例 + 7 verify 分离 Provider/缺绑定/缺凭据/
  维度不匹配/解密失败/停用/无绑定 + 5 gate 三态），exit 0
python -m pytest backend/tests/scripts/test_seed_prompt_eval_db.py \
  backend/tests/test_prompt_eval_workflow.py → 6 passed（既有行为无回归）
# CI（run 38150412116，PR 免费预检 Job 内，同一组 19 项）
Targeted regression tests (19, free) → ✓ passed，无付费调用
```

**BLOCKED/UNVERIFIED 清单**：
- 真实评测 Run：**已解除 BLOCKED**（38150689331 ✅）。
- 精确 API 费用数值：**UNVERIFIED**（P1-1 范围，不视为零成本）。

**合并建议：READY_FOR_REVIEW（最终收口后）**——复核第 5 项要求的真实评测 Run 已完成（run 38150689331 ✅，96.5% PASS，受信任入口执行），P0 阻断项清零（§9）；P1 全部已批准另立任务、不重复计入。不自行合并，等待 GPT 最终验收。

## 10. 第二轮整改逐条回应（GPT 复核意见，commit `93e3dc7`）

| # | 复核意见 | 整改与证据 |
| --- | --- | --- |
| 1 | verify() 硬编码 specialized-api，与 CI seed 的分离 Provider 不符 | `seed_eval_model_bindings.py` 重构：凭据按每个绑定**各自的 provider_id** 逐一解密校验（`evaluate_bindings` 纯函数）；新增必需双绑定缺失校验。测试 `test_split_provider_ids_pass` 等 7 项 |
| 2 | 路径过滤补齐 MCP RAG 与 seed 脚本 + 正反测试 | 判定逻辑抽为 [`.github/scripts/rag_smoke_path_filter.py`](../../../.github/scripts/rag_smoke_path_filter.py)（单一事实源，workflow 与测试共用）；补齐 `mcp_servers/servers/rag.py`（MCP RAG 实现真实路径）与 `backend/scripts/seed_prompt_eval_db.py`（修正原误写的 `scripts/` 前缀）；测试 `tests/test_rag_smoke_path_filter.py` 7 项正反例（import + subprocess 双通道） |
| 3 | prompt-eval Environment Secrets 保护核实 | 实测 `protection_rules: []`、`deployment_branch_policy: null`——**整改前同仓库未审核 PR 可直接获得付费 Secrets**（run 38142127992 日志 secret 被 mask 即实证）。整改：不改用 `pull_request_target`，改为**触发器收敛**——`pull_request` 只跑免费 E0 预检（不引用任何 secrets），真实评测限 `push(main)`/`workflow_dispatch`（受信任入口）。实证 run [38146905426](https://github.com/Jayus-07/agent/actions/runs/38146905426)：PR 上免费预检 ✓、真实评测 job skipped |
| 4 | 回归测试四项要求 | verify：分离 Provider PASS / 缺绑定 FAIL / 缺凭据 FAIL / 维度不匹配 FAIL（+解密失败/停用/无绑定）；路径过滤：MCP RAG 与 seed 脚本必触发 / 纯文档与前端及非 RAG 后端跳过。合计 14 项新测试，19 passed（含 gate 三态 5 项），exit 0 |
| 5 | CI 真实评测仍 BLOCKED，不得宣称 P0=0 或 CI PASS | 已修正：§9 P0 级未闭环 2 项如实列明；本报告不再出现「P0=0」「CI PASS」宣称；合并建议改为 **BLOCKED** |
| 6 | Billing V2 费用归因未完成的验收范围 | §9 明确：**本轮验收范围不含 Billing V2 精确费用归因实施**（P1 挂账另立任务）；费用缺口 UNVERIFIED，未决前不视为零成本、不宣称已记账 |

**整改后 HEAD**：`93e3dc7`（代码与测试）；审查包随最新文档提交更新。

## 11. 最终收口记录（GPT 第二轮复核意见，代码 `0fc0765`/`4a6fd18`，报告 `HEAD`）

| # | 收口要求 | 落实与证据 |
| --- | --- | --- |
| 1 | PR 免费预检 Job 加 19 项定向回归测试 | `rag_smoke.yml` 免费预检 job 新增 Targeted regression tests 步骤（7 路径过滤 + 7 verify + 5 gate 三态）；无付费模型调用、不读 API Secrets、不扩大为全量 pytest。CI 实证：run [38150412116](https://github.com/Jayus-07/agent/actions/runs/38150412116) 该步骤 ✓ |
| 2 | prompt-eval Environment 访问范围审计 + 保护建议 | 引用面：rag_smoke 真实评测段 / rag_regression / prompt_eval 三处（§8）；实测无 protection_rules；给出 Required reviewers ≥1 + Deployment branch policy（restricted：main、task/*）建议，由仓库所有者配置，施工方不代改；正式执行方式随之固定（§8 建议 4） |
| 3 | 真实 CI 执行方式确认 | 已按"人工审核 PR HEAD → workflow_dispatch 选定 PR #30 分支 + regression suite"执行：run [38150689331](https://github.com/Jayus-07/agent/actions/runs/38150689331) ✅ 96.5% PASS（GPT 两轮复核通过后的 HEAD）；正式保护规则配置后照 §8 建议 4 固定流程 |
| 4 | Rerank Key 配置 | 仓库所有者已在 prompt-eval environment 配齐三个 API Key（含首次误填 BASE_URL 的恢复：三个端点值已由施工方重设为生产绑定同款端点） |
| 5 | 不得宣称 P0=0 / CI PASS | 已修正并闭环：真实评测 Run 完成（§8/§9）后 P0 阻断项如实为 0，P1 单独列示不重复计数 |
| 6 | Billing V2 验收范围 | §9 P1-1 唯一归属，明确不含于本轮验收；费用 UNVERIFIED、不视为零成本、不作为合并阻断 |

**最终 HEAD**：见 PR 页（代码 `0fc0765` + `4a6fd18`，报告随最后提交）。

## 12. 真实评测触发矩阵（2026-10-11 用户拍板，随本 PR 落地）

| 触发场景 | 是否运行真实 API | 测试范围 | 落地方式 |
| --- | --- | --- | --- |
| 普通代码 PR | 不需要 | T0/T1、定向单元测试 | ✅ PR 免费预检（run 38150412116 同款）+ 真实评测 job skipped |
| 纯文档、页面样式 PR | 不需要 | 静态检查 | ✅ 路径过滤直接 skip（tests/test_rag_smoke_path_filter.py 锁定） |
| RAG 检索算法、切分策略修改 | 需要 | RAG Smoke + 必要的 Regression | 合并进 main 后自动 RAG Smoke（push 触发）；Regression 由审核后 workflow_dispatch 按需运行 |
| Embedding、Rerank 模型切换 | 必须 | 索引重建 + Golden 回归 | 每次运行已强制索引重建（§4）；probe 维度三方校验失配即 BLOCKED；Golden 回归 = dispatch selection=regression |
| SQL Agent、Planner 修改 | 按风险运行 | 对应领域的真实模型评测 | Runner 已有（runners/sql.py、planner.py），CI workflow 未建（P1-4，另立任务） |
| **每日定时回归** | **不在 GitHub 定时运行**（用户拍板） | 固定 Golden，监测质量 | **`rag_regression.yml` 已移除 `schedule` 触发**；定时回归由**管理端定时任务**承担（配置入口：管理端），费用与监控归项目自身体系 |
| 重要版本发布前 | 需要 | 受影响领域的真实 API 和 E2E | 审核后 workflow_dispatch（selection 按受影响域）；E2E 属发布门禁（release-quality） |

**随本次拍板的代码变更**：`rag_regression.yml` 移除 `schedule`（cron）触发——GitHub 侧不再每日自动消耗 API 费用；本 workflow 仅保留人工 `workflow_dispatch`（受信任入口），本报告 §8 的 run 38150689331 即该入口的首次成功运行。
