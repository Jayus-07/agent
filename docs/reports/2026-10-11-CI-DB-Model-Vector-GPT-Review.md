# CI / DB 模型 / 向量库施工 GPT 审查包（2026-10-11）

> 第 3 阶段施工：CI / 自动评测门禁修复 + DB API 模型口径切换 + 隔离向量库。
> 本报告为随 PR 提交的独立审查材料。所有数字对应 HEAD `53810de01b9acaac82a643c2acbf5434f2458485` 提交的源码。

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

- 旧失败证据：https://github.com/Jayus-07/agent/actions/runs/38086882164（14 连败最后一次）
- 本 PR 触发的 workflow run：见 PR checks（`RAG PR Smoke` 将随 PR 自动触发；`RAG Regression` 可 workflow_dispatch）。
- **依赖前提（需要仓库设置）**：新 workflow 需要以下 Secrets/Vars（`prompt-eval` environment）。**未配置时 run 会以 BLOCKED 失败（seed/probe 步骤显式报错），这是预期行为，不视为代码缺陷**：

| 类型 | 名称 | 说明 |
|---|---|---|
| Secret | `PROMPT_EVAL_TEST_API_KEY` | 聊天/生成模型 API Key（eval_gen） |
| Secret | `PROMPT_EVAL_TEST_BASE_URL` | 聊天模型 base_url |
| Secret | `PROMPT_EVAL_TEST_EMBEDDING_API_KEY` | Embedding API Key |
| Secret | `PROMPT_EVAL_TEST_EMBEDDING_BASE_URL` | Embedding base_url |
| Secret | `PROMPT_EVAL_TEST_RERANK_API_KEY` | Rerank API Key |
| Secret | `PROMPT_EVAL_TEST_RERANK_BASE_URL` | Rerank base_url |
| Secret | `SECRETS_ENCRYPTION_KEY` | 凭据加密主密钥（Fernet，与本地 .env 同口径） |
| Var | `PROMPT_EVAL_TEST_MODEL` / `PROMPT_EVAL_TEST_EMBEDDING_MODEL` / `PROMPT_EVAL_TEST_RERANK_MODEL` | 模型名（如 qwen3.7-text-embedding） |
| Var | `PROMPT_EVAL_TEST_EMBEDDING_DIMENSIONS` | 绑定维度（如 1024，probe 实测校验） |

注：`PROMPT_EVAL_TEST_API_KEY/BASE_URL/EMBEDDING_*` 中部分 prompt_eval workflow 已在使用（如已配置无需重复）；新增的是 RERANK 三项与 `SECRETS_ENCRYPTION_KEY`（若未配）。

---

## 9. P0 / P1 遗留清单

**P0（阻断级）遗留：0 项。**

**P1（登记不扩大本轮）**：
1. 评测链路 embedding/rerank 直调未接入 Billing V2 账本（llm_usage 0 记录）——费用精确归因 UNVERIFIED（§6）。
2. 共享库 rag_eval_kb 恢复/清理拍板（§7 恢复建议）。
3. RAG Regression 历史基线对比（--compare/--promote-baseline）未启用——当前定位为固定样本门禁，如需趋势对比另立任务。
4. 评测生成层（EvalGen）在 CI 口径下无生成模型（--no-ragas 口径）——端到端生成答案评测需显式 opt-in（--judge/--ragas），当前检索评测与生成评测已分离报告。
5. RC-039（采购合同付款节点）语义召回不足——真实检索水位案例，属检索质量优化任务，不在本轮范围。

**BLOCKED/UNVERIFIED 清单**：
- GitHub Actions 真实 API Run：BLOCKED on Secrets 配置（用户在 GitHub 设置提供后触发）。
- 精确 API 费用数值：UNVERIFIED（账本缺口，见 §6）。

**合并建议：READY_FOR_REVIEW**（代码修复有真实评测+测试证据；CI 真实 Run 待 Secrets 配置后触发核对，属部署侧前置条件而非代码缺陷；本轮不自动合并，等待 GPT 复核）。
