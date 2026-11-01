# 任务：客服评测集升级（cs-evaluation-upgrade，供执行会话接手）

- 任务 ID：`cs-evaluation-upgrade`
- 来源：2026-11-01 用户指令「客服相关评测集要升级——评估是否满足现有客服系统以及结合管理端评测的评测，根据结果结合真实项目进行优化」；评估已由上一会话完成（结论见 §1），本任务承接优化实施
- Worktree：`D:\Program Files\workplace\agent-gov`（复用，勿新建；基线见 §8）
- 分支策略：每个可独立交付的优化项独立分支 `feat/cs-eval-pN`（自最新 main），独立 PR 合并 main；**合并前显式逐项确认全部 CI 结论**（含 skipping 项核对是否非 RAG 预期）
- 状态：**评估已完成，P1–P5 待实施**（P1 可直接开工，见 §3/§8）

---

## 1. 背景与评估结论（上一会话实测，免重探）

一句话：**卷子已出好且锁版（320 条），但从未让考生做过——首次全量回放 intent 命中 40.6%**。

三层现状：

| 层 | 现状 | 判定 |
|---|---|---|
| 资产层 | cs 数据集 v2.0 锁版 320 条（20 条 v1 + 300 条 v2；六类 faq60/query60/action60/complaint40/multi_turn40/safety40；意图 20/20 覆盖；sha256 锁版）；断言维度全（cs_route/intent/decision_layer/entities/missing_slots/next_action/risk_level/should_handoff/target/allowed_facts/forbidden_facts/must_(not_)contain + 多轮 turns） | ✅ 成熟 |
| 词表/规则层门禁 | cs-gate 回放 416 条（intent_triage 324 / handoff_timing 50 / confirm_cancel 42），阈值（≥0.9 / 漏转=0 / 误转≤0.1 / ≥0.95），CI 门禁 `backend/tests/customer_service/test_cs_eval_gate.py` 消费 `vocab_gate.py` | ✅ 已闭环，勿动 |
| 决策层评测消费 | **断链**：320 条锁版断言唯一消费者是 test_regression.py 的 3 个存在性测试（能加载/能过 validate）——断言零执行；管理端评测平台 `evaluation_v2.eval_datasets` 现有 9 个数据集全是 rag，**cs = 0**；数据集 manifest 自述「域图 live 消费接线为后续任务」 | ❌ 本任务核心 |

### 1.1 定量基线（2026-11-01，CSRouter.route 完整生产入口离线回放 320 条锁版）

**intent 40.6% / target 52.8% / risk 63.4%**。分类目：

| 类目 | n | intent | target | risk |
|---|---|---|---|---|
| action | 60 | 48.3% | 46.7% | 45.0% |
| complaint | 40 | 62.5% | 30.0% | 37.5% |
| faq | 60 | 16.7% | 83.3% | 88.3% |
| multi_turn | 40 | 30.0% | 37.5% | 50.0% |
| query | 60 | 41.7% | 58.3% | 98.3% |
| safety | 40 | 22.5% | 55.0% | 22.5% |
| v1 | 20 | 100% | 35.0% | 100% |

### 1.2 三类系统性偏差（P2 修订对象，勿逐条零散改）

1. **k_policy / k_faq 边界错位**（faq intent 仅 16.7%）：「退款一般多久能到账」期望 k_policy、规则层给 k_faq——标注口径与规则词表口径不同源。
2. **域判定门槛与自然表达脱节**：「我的订单 DEMO-1012 现在什么情况」「帮我把钱退回来」未过粗分强信号门槛（`CS_RULE_MIN_HITS`）落 k_faq 兜底——**真实用户同样会踩，属真实系统能力缺口**，非纯标注问题。
3. **断言口径待修**：safety 类期望 intent=k_policy（注入样本走知识域拒绝的语义需重审）；multi_turn 按末轮断言但离线回放无上下文（口径错位）；**数据集治理悬空**：`v2/` 目录存在 304 条修订分片与锁版 320 并行（交集 300；loader 只认 cases.jsonl），从未走锁版生效流程。

### 1.3 分工红线（避免重复建设）

幂等/资金/坐席状态等工程正确性由既有 119 个 `backend/tests/customer_service/` 单测守（确定性 T2）；**评测集只管「模型与路由的行为质量」**。

---

## 2. 已知信息（执行会话免重探，2026-11-01 实证）

### 2.1 数据集与代码事实（文件:行号级）

- 数据集：`backend/evaluation/datasets/cs/{cases.jsonl, v2/*.jsonl, gate/*, manifest.json}`；loader `backend/evaluation/dataset/loader.py::load_dataset`（V3 优先级：selection → cases.jsonl → 目录分片；`.deprecated.` 自动跳过）；锁版流程见 `datasets/README.md` 维护约定（改用例必升版本号 + 重算 sha256 + manifest case_count 同步）
- 回放链路（零 LLM 可回放 decision_layer=rule 的 304 条）：`CSRouter.route(query)`（`backend/customer_service/router/cs_router.py:38`，完整生产入口，内部串 DomainDetector→CSCoarseRouter→CSFineRouter，产出 CSRouteResult：intent/route_path/risk_level；有 Redis 查询缓存与 trace span 软失败）→ `resolve_route_path(intent)`（intents.py）；expected.target 值域 cs_knowledge/cs_business_query/cs_business_action/… 与 CSRoutePath 的映射见探针脚本 TARGET_MAP（cs_knowledge←knowledge_query / cs_business_query←business_query / cs_business_action←business_action；complaint/human 两个映射值待 P1 时与 v1 target 实测值核对）
- gate 消费方：`backend/customer_service/vocab_gate.py`（回放器 + `evaluate_vocab_change` 词表热更 fail-closed 门）——**P3 词表扩圈必须经它回放防回归**
- 评测平台持久层：`evaluation_v2.eval_datasets/eval_dataset_versions/eval_cases/eval_case_results/...` + `ai.eval_run_records/eval_run_samples`；管理端 `/evaluations/center` 三 Tab（数据集 DatasetsTab / 运行评测 RunsTab / 结果对比 CompareTab，组件 `frontend-admin/src/components/workbench/evaluation/proto/`；DatasetsTab 模块筛选已含 cs；RunsTab 有 offline/self+ragas 模式，isRag 有特判、cs 走通用模式）；评测 API：`backend/app/api/routes/evaluation.py`、`evaluation_datasets.py`
- 管理端评测平台 9 个已注册数据集全为 rag（dataset_key 见 smoke_refuse_*/synthetic_*/ui_test_refuse 等，均 domain='rag' active）——cs 注册路径参照它们（UI/API 均可）
- 未解决坏例来源：`ai.unanswered_questions`（070 迁移，session_id/question_hash/source=rag_miss|sql_empty）

### 2.2 回放探针脚本（已持久化）

`scripts/cs_dataset_probe.py`（本目录）——上一会话的探针（v3：CSRouter 完整入口 + 320 锁版 + v2/ 分片差异盘点 + 按类目基线 + 失败样例）。P1 固化入 CI 时以此为基础改造（探针 → pytest 参数化门禁）；跑法见脚本头注释，需在 worktree 根用主仓库解释器执行。

### 2.3 环境坑（沿用 capability-governance TASK §2.4，全部本会话实证）

1. 写文件一律 Write/Edit（Mimosa hook 拦 Bash 直写，含个别只读命令误判）；
2. GitHub 间歇断连（11-01 曾 443 连不上约 20 分钟）——push/PR 用「>log 2>&1 + rc 判定 + 30-90s 退避 ×5」；
3. `devctl.bat rebuild` 在 Git Bash 管道下静默失效——用等价 `COMPOSE_PROJECT_NAME=agent docker compose -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.local.yml build/up`，容器内 grep 标记验证换血；`MSYS_NO_PATHCONV=1` 防 docker exec 路径转换；
4. 真库 5433（非 5432）；psycopg2 连接带 connect_timeout；worktree 无 .env 从主仓库拷；frontend-admin node_modules 用 junction 复用主仓库；
5. 测试解释器 `D:\Program Files\workplace\agent\.venv\Scripts\python.exe`，cwd 在 worktree 根；
6. 并行会话多（travel-v2 生产化、ci-debt 等）：动共享容器/库前先核查归属；全量 pytest 期间冻结；live overview 曾见 govtest 测试数据属并行会话，未清理。

### 2.4 关联上下文

- 前置任务 capability-governance（G1–G6）**已全部合入关闭**（PR #198/#200/#202/#203/#204/#207 + 回填 #199/#211/#216），见同目录姊妹任务 `docs/tasks/capability-governance/`
- 客服域投产就绪度评估（同日完成，结论供范围决策）：功能/质量面达标可受控试用；硬缺口=资金真实后端（商城域另立项，开关保持 disabled 即不阻塞 web 渠道首期）、容量压测零数据、安全渗透测试未做；**frontend-cs 生产服务定义已存在**（`部署/prod-4c8g/docker-compose.local.yml:223`，本机与生产共用资源层，README 启动清单含 frontend-cs）但本机从未起过该容器——生产构建验证待做（上一会话 build 被中断未完成，见 §3 P6）
- 真实渠道决策：**目前只有 web**（用户 2026-11-01 确认）；channel 枚举其余值（app/phone/wechat）接入哪个验哪个
- travel-v2 侧连带项：G5 翻正 stats_available 已写入 `docs/tasks/travel-v2-production/TASK.md` 阶段四 E5

---

## 3. 优化方案（P1–P5，按优先级）

| 项 | 内容 | 成本 | 依赖 |
|---|---|---|---|
| P1 | 离线决策层回放 runner 入 CI（固化探针 → pytest 门禁，阈值=当前基线防退化；v2/ 分片与锁版关系定案） | ≤1 天 | 无 |
| P2 | 数据集修订三类系统性口径（k_policy/k_faq 边界、safety 期望语义、multi_turn 多轮回放口径）+ 走锁版流程升 v2.1 | 1-2 天 | P1 |
| P3 | 真实系统能力补强：扩 CS_DOMAIN_PATTERNS/词表覆盖自然表达（经 cs-gate 回放护航 fail-closed） | ≤1 天 | P1（防回归护航） |
| P4 | 管理端评测中心接线：cs 数据集注册（domain='cs'）+ offline 基线跑批 + live 跑批（320×LLM 有预算成本，**需用户拍板频次与预算**） | 1-2 天 | P1/P2 |
| P5 | 坏例回流运营闭环：unanswered_questions 周度评审 → 数据集增补（锁版升版本流程） | 长期运营 | P2 |

## 4. 分期详情与验收

### P1 离线回放 runner 入 CI（开工第一件事）

- 改动：①`backend/tests/customer_service/test_cs_decision_replay.py`——探针固化为 pytest 门禁（参数化回放 320 条；断言维度 intent/target/risk 按类目聚合命中率 ≥ 基线阈值：**intent≥0.40 / target≥0.52 / risk≥0.60**，防退化不防历史；失败样例打印 ≤20 条供修订）；②v2/ 分片定案：与锁版 diff（交集 300/仅分片 4/仅锁版 20——仅锁版的 20 条即 v1）→ 将 v2/ 目录标记 deprecated（改名 `.deprecated.` 中缀）或按其内容修订锁版后删除，**二选一须在 PR 描述写明依据**；③`datasets/README.md` 模块表补 cs 消费方式行
- 测试：新门禁测试自身绿 + 治理批（test_cs_eval_gate / test_regression / test_layer_consistency）不回归
- **验收**：CI 显式全绿；回放基线数字与 §1.1 一致（±2%，防数据集/链路漂移未被察觉）

### P2 数据集修订升 v2.1

- 三类口径修订按 P1 失败清单执行；修订走锁版流程（升版本号 2.1 + 重算 sha256 + manifest notes 记录修订依据）
- **验收**：v2.1 回放 intent/target 命中率显著提升（预期 ≥70%——修订口径后规则层真实水平）；README 维护约定全遵守；**不得以改断言迁就现状**（修订的是口径错位，不是把期望改成系统现状的复写——每条修订在 notes 给依据）

### P3 词表/规则能力补强

- 扩圈对象：§1.2 第 2 类失败样例（自然表达→兜底 k_faq 的 Top 模式）；改动经 cs-gate 三份回放集护航（vocab_gate evaluate_vocab_change fail-closed）
- **验收**：v2.1 回放 intent 命中提升且 cs-gate 三门禁全绿零回归

### P4 管理端评测中心接线

- cs 数据集经评测 API/UI 注册（domain='cs'，参照现有 rag 数据集的 tenant/status 口径）；offline 模式跑基线 run；live 跑批（self 模式 320 条经真实 LLM+域图）**先向用户确认预算与频次再动**
- **验收**：管理端可见 cs 数据集与 run 结果；live 基线数字与 DB 抽查一致；allowed_facts/forbidden_facts 断言在 live 模式生效

### P5 坏例回流（长期）

- 周度（或按需）从 ai.unanswered_questions 拉高频未解决问题 → 评审 → 增补 v2.1+ 用例（走锁版流程）；输出到管理端数据集页可运营

## 5. 技术约定

- 唯一事实源：锁版数据集是客服评测断言唯一事实源；修订必须走锁版流程；禁止 runner 内手写第二份期望
- 回放分层诚实：规则层离线断言与 LLM live 断言分开报告，不混算；LLM 依赖维度离线跑时显式 skip 计数
- 全部只读/本地计算（P4 live 有 LLM 预算）；不加新依赖
- 每期：实现 → 测试 → diff 出示 → 用户确认后提交（双重路径限定）→ PR → CI 显式全绿 → 合并 → PROGRESS 回填
- 未实际执行的验证不得报通过

## 6. 测试策略

- 门禁测试入 `backend/tests/customer_service/`（照 test_cs_eval_gate 风格）；数据集加载走统一 loader 不绕过
- 回归面：cs 治理批（test_cs_eval_gate + test_regression + test_layer_consistency + test_registry_consistency）不回归；评测平台 API 面（backend/tests/api/test_registry_overview_api 等）不回归
- 真库测试 p9g- 前缀用后即清（如涉及）

## 7. 总验收标准

1. P1–P5 逐项合入，验收矩阵回填 PROGRESS；
2. 320 条（或 v2.1 修订后条数）锁版断言在 CI 有真实执行（不再只是存在性测试），基线阈值生效防退化；
3. 数据集治理悬空清零（v2/ 分片定案，manifest 与实际一致）；
4. 管理端评测中心可见 cs 数据集与运行结果（P4 offline 部分）；live 跑批按用户拍板的预算执行；
5. cs-gate 三门禁全程零回归；
6. 文档同步：datasets/README.md（cs 消费方式与 v2.1 修订记录）、evaluation-governance.md（如涉准入流程变化）、customer-service.md（如涉词表能力变化）；
7. 主链路冒烟零回归（/health、:9080、:3200 全 200）。

## 8. 会话交接（启动清单）

1. 核对：`git -C "D:\Program Files\workplace\agent-gov" fetch && git log --oneline -3 origin/main`；worktree 当前停靠 `docs/cap-gov-owner-confirm`（已合并，切新分支 `feat/cs-eval-p1` 自 origin/main）；工作树干净（上一会话收尾时确认）
2. 通读本 TASK + PROGRESS.md；探针脚本 `scripts/cs_dataset_probe.py` 先跑一遍复现基线（数字应与 §1.1 一致，偏差 >2% 先查数据集/链路是否被并行会话改动）
3. P1 开工：探针 → pytest 门禁 + v2/ 分片定案 + README 补行；每期按 §5 流程
4. 暂停时按仓库规范记录 PAUSED 现场（HEAD/未提交/测试证据/下一步）；本地已合并分支（feat/cap-gov-g1~g6、docs/cap-gov-* 共 9 个）可清理但非必须——下会话自行决定，勿动其他会话分支（chain/codex/*/docs/ci-debt-* 等）

## 9. 决策点与风险

| 决策点/风险 | 处置 |
|---|---|
| P2 修订是否「迁就现状」的边界 | 修订对象是口径错位（k_policy/k_faq 边界、safety 语义、multi_turn 口径），每条修订给依据；规则层真实能力缺口归 P3 补，不反向改断言 |
| P4 live 预算 | 320 条 × LLM 调用非零成本，频次/预算必须用户拍板后才动 |
| 规则层改动的回归风险 | 全部经 cs-gate fail-closed 门回放护航；不通过即拒绝写盘（机制已存在） |
| 并行会话干扰 | travel-v2/ci-debt 在途：动共享容器/库前核查归属；评测平台表写入前确认无他人同域数据集操作 |
| 基线漂移 | P1 门禁阈值防退化；并行会话若改路由链路导致基线变化，以「阈值更新 + 依据」走 PR 而非静默放宽 |
