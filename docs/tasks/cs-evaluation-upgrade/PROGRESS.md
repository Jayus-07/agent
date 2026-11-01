# PROGRESS — cs-evaluation-upgrade（客服评测集升级）

- 任务文件：同目录 TASK.md（唯一事实源）；评估由上一会话完成，本文件承接实施进度
- 更新时间：2026-11-02
- 当前状态：**P1–P6 已完成；下一会话从 P7（case 归档端点）开工——P7/P8 为运营收尾最后两项（TASK §10）**

## 阶段状态总表

| 项 | 主题 | 状态 | 分支 / PR | 证据 |
|---|---|---|---|---|
| 评估 | 评测集满足度评估（资产/消费/接线三层 + 320 条回放基线） | **已完成**（上一会话，结论入 TASK §1） | — | scripts/cs_dataset_probe.py 可复现 |
| P1 | 离线回放 runner 入 CI + v2/ 分片定案 | **已完成**（2026-11-02） | feat/cs-eval-p1 / [PR #223](https://github.com/Jayus-07/agent/pull/223)（merged 63df24b） | 见日志 2026-11-02（一） |
| P2 | 数据集修订三类口径 + 升 v2.1 | **已完成**（2026-11-02） | feat/cs-eval-p2 / [PR #228](https://github.com/Jayus-07/agent/pull/228)（merged a729f3c） | 见日志 2026-11-02（二） |
| P3 | 词表/规则能力补强（cs-gate 护航） | **已完成**（2026-11-02，含候选 4 条收编升 v2.2） | feat/cs-eval-p3 / [PR #229](https://github.com/Jayus-07/agent/pull/229)（merged b770788） | 见日志 2026-11-02（三） |
| P4 | 管理端评测中心接线（offline 先行；live 需用户拍板预算） | **已完成（offline）**（2026-11-02） | 纯运营操作无代码分支；见日志 2026-11-02（四） | run 1a2ae7da：324/324 pass |
| P5 | 坏例回流运营闭环 | **机制闭环已验证**（2026-11-02，长期运营） | 无代码（复用 manual candidate 通道）；SOP 入 evaluation-governance.md | 见日志 2026-11-02（四） |
| P6 | P6 live 跑批（预算 10 元）+ live 通道核实收口 | **已完成（部分验收缺口移交）**（2026-11-02） | feat/cs-eval-p6 / [PR #244](https://github.com/Jayus-07/agent/pull/244)（merged）+ 运营执行 | 324 条全量 197/324（v1 9/20 与离线一致、CS2 188/304=61.8%）；成本 ≈1.05 元；见日志 2026-11-02（六） |
| P7 | 评测平台 case 归档端点 + 清理约 400 个重复 approved case | 待实施（下一会话开工） | feat/cs-eval-p7（计划） | TASK §10 P7 |
| P8 | 持久化 intake（unanswered_questions → 候选） | 待实施 | feat/cs-eval-p8（计划） | TASK §10 P8 |

状态取值：待实施 / 进行中 / 待验收 / 已完成 / BLOCKED（注明原因）/ PAUSED（注明现场）。

每项完成后回填：分支与 PR、实际测试命令与 PASS/FAIL、基线数字对比、遗留事项。

## 环境

- Worktree：`D:\Program Files\workplace\agent-gov`（复用；P6 收尾停靠 docs/cs-eval-p6-wrapup，新分支自最新 origin/main）
- P6 宿主跑批环境三件套（NO_PROXY / 注册表刷新 / 8088 模型服务）与包装脚本说明见日志 2026-11-02（六）
- 基线时点：origin/main@b770788（P3 合并后；并行会话 travel-v2/ci-debt 持续推进，开工时先 fetch 对齐）
- 测试解释器：`D:\Program Files\workplace\agent\.venv\Scripts\python.exe`（worktree 无 .venv，需从主仓库拷 .env）；真库 5433
- frontend-admin node_modules：junction 复用主仓库（如涉评测中心前端改动）
- 关键口径：回放链 CSRouter.route 完整入口；词表门禁 vocab_gate fail-closed 勿绕过；锁版修订流程见 datasets/README.md
- 环境坑：TASK §2.3（Mimosa hook / GitHub 抖动重试 / compose 等价原命令 / MSYS_NO_PATHCONV / connect_timeout / 并行会话纪律）——开工必读

## 日志

### 2026-11-02（六）—— P6 落地：live 跑批 324 条全量 + runner live 契约四处修复（PR #244）

- **P6① 通道核实结论（推翻立项预设）**：v2 平台对非 rag 模块**硬编码 live=False**（`evaluation_v2/runner.py:414`，注释「禁止通过 live 模式触发模型或外部服务」；cs ∈ `CONTRACT_ONLY_RUNNER_MODULES`，快照 environment 校验强制 contract_only 一致）；`evaluation_mode`（semantic/ragas/self+ragas）非 rag 只存快照不消费——**管理端/`POST /v2/runs` 发不起 cs live run，P4 的 324/324 offline run 即 `_run_offline_sanity` 结构校验**。live 唯一现成通道 = legacy CLI `python -m backend.evaluation cs --live`（本次采用，零平台代码改动）。
- **runner live 契约四处缺陷（全部经 live 实测暴露、[PR #244](https://github.com/Jayus-07/agent/pull/244) 修复合入 main，5 commits，CI 7/7 绿）**：
  1. `_run_live` 读 expert_history 按 `{"node": ...}`，现图契约（experts/base.py）是 `{"expert": ...}`——路由断言全废（b0 实测 0/20「实际命中 [None]」）；
  2. history 条目值是语义短名（knowledge/query/...），断言期望图节点名——经 `EXPERT_NAME_TO_NODE` 权威映射归一（b0r 实测 visited 只见 'knowledge'）；
  3. runner 传 `cs_route={}` 旁路生产决策层——supervisor 全靠 L3 LLM/L6 知识兜底重路由，expert_match 掉到 10%（b0r2）；修为 `get_cs_router().route(q).model_dump()` 生产 prefilter 口径；
  4. thread_id/session_id 固定值跨 run/跨 case 污染——checkpointer 续上上次 pending 投诉单（b0r3，45ms 接管）、confirmation store 按 (user_id,session_id) 键控使同 run 先跑 case 的动作提案拦住后续全部样本（b0r4/r5，47-102ms 秒回）；修为 run token + case 级隔离。
  - 回归测试：`backend/tests/customer_service/test_cs_eval_runner_live.py` 6 例（三种历史形态归一/cs_route 接线断言/错路由仍 fail）+ `test_cs_runner_v2` 相邻回归，**8/8 绿**。
- **宿主执行三件套（live CLI 在宿主跑通的前置，均一次性实证）**：①启动宿主本地模型服务（`dev-local-models.bat`→scripts/local_models.ps1，端口 8088——rag-service 的 bge-m3 embedding binding 指向 host.docker.internal:8088，挂了会让知识链路 500）；②`NO_PROXY="*"`（宿主 Windows 系统代理被 httpx/requests 自动拾取且 TLS 被掐，EOF violation；容器不受影响）；③执行前 `refresh_registry()`（容器由服务启动时刷新，宿主 CLI 缺刷会把 DB 登记的 custom-* 模型误落到 ollama 兜底 502）。三件已封装 `d:/tmp/cs-live/run_eval.py` 包装脚本（刷新+LLM 冒烟门+转发 CLI；**不入库，若复用先提炼**）。
- **跑批执行与结果（llm_usage 对账）**：
  - v1 前 20 条（cs_live_b0 selection）：**9/20 = 45%**，与离线回放**逐条一致**（同一 miss 清单：CS-002/007/009/011/012/013/014/015/016/018/020，均为规则层与 v1 老标注的真实分歧，非 runner 缺陷）；
  - CS2 304 条（b1-b4 selection 80/80/80/64）：**188/304 = 61.8%**——faq 88.3% / complaint 85.0% / query 72.6% / safety 57.5% / **action 38.7%** / multi_turn 22.5%（结构性：runner 单轮执行，逐轮重放待 runner v2.1，与 P2 口径一致）；
  - action 缺口的主体是「低置信度→放行知识兜底」生产行为（离线回放看不到的真实 live 信号）+ P3 词表（t_logistics 等）与 P2 修订（C1-039/045 类 k_faq）的边界张力——**需产品/规则层决策，不在本任务内改**；
  - 成本：全时段 llm_usage 合计 **$0.1454 ≈ 1.05 元**（10 元预算内；批窗口 250 次 LLM/55,285 tokens=$0.1374；超 8 元中止线未触发）。
- **抽查 12 条（P6④）**：断言机制生效（路由/must_contain 与 per_case 报告、日志、llm_usage 一致）；发现**「系统繁忙」兜底应答 101/304**——根因是 rag-service 的 cs_faq KB **BM25 索引缺失**（runtime 只读索引不可用，基建缺口非 runner 缺陷），知识应答质量被拉低但路由断言不受影响。
- **验收口径**：①花费 ≤10 元 + llm_usage 对账 **达成**；②基线数字与 live 抽查一致 **达成**（v1 20 条与离线逐条吻合）；③「管理端可见 live run」**不达成**（legacy run 不进 v2 runs 列表——v2 平台非 rag live 接线缺口）；④allowed/forbidden_facts live 断言**不可达**（full_path 模式无调用方）。
- **移交清单（按优先级，含根因）**：
  1. **v2 平台非 rag live 通道**：要么接线（`_execute_non_rag_run` 放开 live + 快照语义扩展 + 管理端 UI 同步），要么明确 contract_only 为终态并在管理端对非 rag 隐藏评测方式选项（当前 UI 可选 semantic 等但不生效，误导）；
  2. **full_path 断言接线**：`_run_cs(mode="full_path")`（唯一消费 must_not_contain/forbidden_facts/clarify 断言的 live 路径）在 legacy/v2 均无调用方——加开关或降口径；
  3. **rag-service cs_faq BM25 索引补建**（「系统繁忙」101/304 的根因，`index/import_fixture`）；
  4. **multi_turn 逐轮重放**（runner v2.1，P2 口径的 live 层兑现）；
  5. **action 38.7% 缺口决策**（低置信度知识兜底 vs 数据集期望，涉及 P3 词表/P2 修订边界，需拍板）；
  6. **副作用清理**：`customer_service.tickets` eval_bot 名下 **60 条**工单（COMPLAINT-*/HANDOFF-* 前缀）、confirmation store eval 残留（地址修改 pending）、checkpointer `eval-cs-*` 线程——并入投产检查清单第 5 项；
  7. 批次证据：`D:\tmp\cs-live\`（b0/b0r*/b1-b4 日志、批次 jsonl、时间窗）+ worktree `data/eval_runs/cs-live-1102-*`（报告落盘，data/ 已 gitignore）。
- **运行态残留说明**：本次未动共享容器（app/rag-service 为并行会话 agent-wt-travel-proto worktree 构建，其 customer_service 代码不含 P3 词表——已在宿主进程以最新 main 代码执行图，无版本混淆）；宿主 8088 模型服务已启动属共享栈依赖修复（增量动作）。

### 2026-11-02（五）—— 用户拍板与运营收尾立项（P6–P8，TASK §10）

- **用户拍板**：①P4 live 跑批预算 **10 元**批准执行；②要求补评测平台 case 删除/归档能力（清理 P4 竞态约 400 个重复 approved case）；③要求补持久化 intake（unanswered_questions → 候选）；④询问管理端发起/历史展示与「做完可否上线运营」。
- **核实结论**：管理端 RunsTab 已有「发起评测」表单（仅 super_admin）与运行列表/环境指纹卡——发起与历史展示 UI 均存在（RunsTab.tsx:124-184）；上线口径见 TASK §10 引言（P6–P8 完成 = 评测运营线可上线运营受控试用；全量公开投产另需压测/渗透/资金决策）。
- **立项**：TASK §10 落稿 P6（live 跑批护栏步骤）/P7（case 归档端点）/P8（持久化 intake）+ 上线运营巡检清单；下一会话从 P6 开工。

### 2026-11-02（四）—— P4 落地（管理端评测中心 offline 接线）+ P5 机制闭环验证

- **P4（纯运营操作，无代码改动）**：cs 数据集经 evaluation_v2 治理 API 注册并入管理端——
  - dataset `cs_locked_v2_2`（domain=cs，tenant=default，owner=user:279 沿用现有 rag 数据集口径）；
  - 锁版 v2.2 全量 324 条经导入（非 RAG 通道：manifest schema `evaluation-v2/1.0`、单批 ≤100 分 4 批、case 字段白名单转换——turns 入 input、category/schema_version 入 metadata）→ 逐条 review approve（approve 需回填 expected=proposed_facts[0]）→ dataset version **v1.0.0 发布（324 items）** → suite `cs_locked_v2_2_full`（复用已发布 deterministic evaluator，binding=answer_quality）→ suite version 发布；
  - **offline 基线 run：run_id `1a2ae7da-e1ed-5bd1-a755-62b2cc0222cb`，324/324 completed、324 passed（100%）**，管理端 runs 列表可见；
  - 身份链：JWT 签发（local_super_admin/user:279）+ auth-redis 会话键（注意：网关会话闸查的是独立容器 `agent-auth-redis`，非宿主 6379 主 Redis）+ `X-API-Key` 服务身份头，全程经 :9080 网关生产口径。
- **P4 过程波折（如实记录）**：脚本重跑竞态导致同批用例被导入 4 次（候选层去重靠防重查询，竞态窗口在「in_review=0」瞬间）；approved 的重复 case 无 DELETE 端点可清——发布时按锁版原 id（payload.source_ref.case_id）去重只选 324 条唯一 case 进 version，重复 case 留在 case 池不可见（**遗留：评测平台缺 case 删除/归档端点，建议平台补齐**）。共享栈期间被并行会话 rebuild 两次（按纪律只读等待恢复，数据无损失）。
- **P5（机制闭环，复用既有通道零新代码）**：`ai.unanswered_questions`（当前 29 条：rag_miss 20 + sql_empty 9，均为测试残留）→ 评审 → 增补数据集的回流链路验证可达：真实坏例 question 经 `POST /v2/candidates`（source_kind=manual）→ review reject 留痕（201/200）。
  - **设计缺口（移交）**：`intake/from-trace` 只认进程内 trace collector 最近 200 条，跨重启的历史坏例无法经 trace 入口回流——持久化坏例走 manual candidate 通道（SOP 已写入 evaluation-governance.md §5 收录段）；建议平台补「intake by unanswered_questions id」。
  - 周度评审 SOP 已文档化（evaluation-governance.md）；当前坏例池为测试残留（与投产检查清单第 5 项关联）。
- **任务计划总收口**：P1–P4 完成、P5 机制闭环；TASK §7 总验收矩阵见最终汇报。**P4 live 跑批（324 条 × 真实 LLM）预算与频次仍待用户拍板。**

### 2026-11-02（三）—— P3 落地：词表/规则扩圈 + 候选收编升 v2.2

- **扩圈**（经 cs-gate 三份 golden 回放护航零回归）：fine 补 ACCOUNT 域细分规则（此前地址/密码诉求全落域默认 a_login_issue，动作类型错）、TRANSACTION 补 t_logistics/t_delivery_estimate 词表、KNOWLEDGE 补 t_delivery_estimate（配 delivery_policy 通道）；修 k_policy 裸「保修」与 k_warranty「保修期」的单命中 dict 序冲突；vocab 域词表补自然表达（订单状态/DEMO 单号/售后自然动作/地址密码/投诉句式/人工坐席/余额/取消订单）；coarse policy_question 排除显式退款动作倾向（先退款/还能退款/到账了吗/退回哪里）。
- **候选收编**：CS2-C2-061/062（t_balance_query）、CS2-C3-061/062（t_order_cancel）扩圈后回放全绿，自 v2.deprecated 分片原样收编，锁版 320→324 升 v2.2（content_hash 0e0ec5bde442b0c1）。
- **门禁基线更新**：intent 75.7%（215/284）/ target 80.3%（228/284）/ risk 79.9%（195/244），阈值 0.74/0.79/0.78——**TASK §4 P2 预期「≥70%」由 P3 兑现**（P2 时 56.8%/62.9%/72.5%）。残余 fail 主体：safety（规则层不判注入，归 live/信号门层）、multi_turn（离线 skip）、情绪投诉等灰样本（live 层 LLM）。
- **探查发现**：`CSRouter.route()` 的 Redis 查询缓存（TTL 300s）会让手工回放读到陈旧路由结果（实测踩坑：扩圈后重放数字不变）——门禁测试已有 `_NoCache` stub 隔离，手工跑探针须注意；探针 docstring 已补提示。
- **验证**：治理批+路由单测+门禁合跑 **148/148 绿**（test_cs_eval_gate 三门槛/regression/layer/registry/cs_runner_v2/新门禁/coarse_router/intents/confirmation）；PR #229 CI 6 pass + 1 预期 skip；合并 b770788。
- **中断事件**：本阶段 push+PR 命令曾被会话意外退出打断——P3 本地提交无损，重试 push 后补建 PR，零损失续工。

### 2026-11-02（二）—— P2 落地：数据集修订升 v2.1

- **修订 75 条**（依据全部入 manifest notes，修订口径错位非迁就现状）：
  - faq k_policy/k_faq 标注口径同源化 39 条：规则层 k_policy=显式政策词命中（fine_router 词表）、k_faq=知识域设计内兜底（intents.py P3.5，KB 覆盖全知识域）；无显式政策词的政策咨询/拒答题→k_faq（拒答由知识域证据门禁执行）；显式动作动词（申请/怎么退/再申请）→动作意图+target 连带对齐；知识咨询语义被域词表截走的 7 条保持知识语义（fail 归 P3，不反向改断言）。
  - intent→target 映射矛盾 22 条：expected.intent 经 INTENT_PROFILES 映射的 target 与 expected.target 冲突（纯标注 bug，系统不可能双命中）——target 对齐映射契约；safety 退款主题 4 条 intent=as_refund 保留、target 改 cs_business_action。修订后数据集内部矛盾 **0**。
  - **safety 语义重审**：双轨标注成立（注入样本走知识域拒绝 / 表面正常样本按语义路由+high risk 交下游权限）；risk=high 断言属安全检测层能力（live/信号门 CS_SIGNAL_GATE，不在 route() 内），规则层静态画像离线不可满足——离线门禁对该维度显式 skip。
  - **multi_turn 口径**：会话级断言需上下文，整类 40 条离线 skip、转 live/graph 层消费（P4）；数据集本体不动。
- **4 条候选评审（P2 时点）**：回放实测全部 coarse no_match→k_faq（「取消」不在域词表、「余额」仅 fine 豁免词表）——不收编，转 P3 验收样本。
- **门禁口径**：intent/target 分母 280、risk 240；基线 intent 56.8%/target 62.9%/risk 72.5%，阈值 0.55/0.61/0.71。
- **验证**：门禁 6/6 绿 + 治理批合跑 60/60 绿；PR #228 CI 6 pass + 1 预期 skip；合并 a729f3c。
- **对 TASK「预期 ≥70%」的说明**：faq 修订兑现（类目 16.7%→85%），剩余缺口主体在 action/query/v1 词表覆盖（a_address/a_password/t_logistics/t_delivery_estimate 无细分词表、coarse 强信号门槛 vs 自然表达）——由 P3 补齐。

### 2026-11-02（一）—— P1 落地：决策层回放门禁入 CI + v2 分片定案

- **开工对齐**：worktree 停靠 docs/cs-evaluation-upgrade（已合并），切 `feat/cs-eval-p1` 自 origin/main@c839dda；GitHub 间歇断连延续（fetch 首轮 5 连败，push 窗口恢复，合并后 fetch 再度连败——按预案退避重试）。
- **探针复现基线**：320 锁版回放 intent 40.6% / target 52.8% / risk 63.4%，逐类目与 TASK §1.1 一致（零漂移）。
- **重要修正（TASK §2.1 预留「complaint/human 映射待核对」的结论）**：探针 TARGET_MAP 将 human_handoff→cs_human，而锁版 target 值域实为 **cs_handoff（17 条，无 cs_human）**——路由正确转人工的用例 target 被系统性误判 miss。修正后基线 **intent 40.6% / target 57.2% / risk 63.4%**（17 条中 14 条实际路由正确）。门禁 target 阈值据此取 0.56（TASK 初拟 0.52 系按错映射口径计算）。
- **P1-① 门禁**：新增 `backend/tests/customer_service/test_cs_decision_replay.py`——统一 loader（manifest content_hash 加载时强制校验）+ `CSRouter.route` 完整生产入口回放 320 条 + 三维防退化阈值（intent≥0.40 / target≥0.56 / risk≥0.60）+ 失败输出类目分布与 ≤20 条样例 + 测试内禁用路由缓存隔离共享 Redis + 2 条覆盖性测试（回放无异常占位 / TARGET_MAP 覆盖 CSRoutePath 全 5 值）。多轮按首轮回放（基线口径，末轮+上下文归 P2）。
- **P1-② v2/ 定案**：盘点实证交集 300 条与锁版**逐字段完全一致**（生成中间产物非修订版）；仅分片 4 条（CS2-C2-061/062 t_balance_query、CS2-C3-061/062 t_order_cancel）为未收编候选→收编归 P2 锁版流程。处置：`cs/v2/` 改名 `cs/v2.deprecated/` 归档（10 文件纯 rename，遵循 README「弃用不删除」约定）。**连带发现** `backend/tests/evaluation/test_cs_runner_v2.py` 直读分片（TASK 未点名的双事实源并行消费者）→ 改写为统一 loader 吃锁版 320，runner 离线覆盖保留（实测 320 全过 sanity 0 fail）；evaluation-v2.yml 按文件路径触发无需改。
- **P1-③ 文档**：`datasets/README.md` cs 行纠偏（20 条 v1.0 → 320 条 v2.0）+ 补消费方式与归档说明；`validator.py` 注释路径同步；探针脚本映射与归档路径同步。
- **验证（全部实际执行）**：新门禁 5/5 绿（29s）；cs 治理批合跑 **57/57 绿**（58.6s：test_cs_eval_gate + test_regression + test_layer_consistency + test_registry_consistency + 新门禁）；test_cs_runner_v2 + 新门禁合跑 7/7 绿；探针重跑对账一致。无需重建容器（改动全为离线件，loader 对 v2.deprecated 零依赖）。
- **CI**：[PR #223](https://github.com/Jayus-07/agent/pull/223) 检查 **6 pass + 1 预期 skip**（RAG Smoke Evaluation 付费评测仅 push/dispatch 触发，PR 不跑——非异常 skip）。首轮 Evaluation V2 free contract tests 失败（runner 测试直读已改名分片），fixup 92d6c3f 后全绿；合并 commit 63df24b。合并后 dispatch `full_regression(suites=customer_service)` 显式执行新门禁：**整场 success**——pytest customer_service job [绿](https://github.com/Jayus-07/agent/actions/runs/41329364907/job/124916011618)（新门禁在 CI 独立 PG+Redis 环境真实执行通过），pytest evaluation job 同绿（改写后 test_cs_runner_v2 吃锁版 320 通过）。
- **未完成/移交**：P2 按门禁失败清单修订三类口径（k_policy/k_faq 边界、safety 语义、multi_turn 末轮口径）并走锁版升版流程；4 条未收编候选（t_balance_query/t_order_cancel）在 P2 一并评审；P3 词表扩圈；P4 live 预算待用户拍板；frontend-cs 生产构建验证仍在投产检查清单第 1 项。

### 2026-11-01（一）—— 评估会话产出（本任务立项依据）

- **评估触发**：用户指令「客服相关评测集要升级：评估是否满足现有客服系统、结合管理端评测，根据结果结合真实项目优化」。
- **探明结论**（详见 TASK §1/§2）：
  - 资产成熟：v2.0 锁版 320 条 + cs-gate 416 条词表门禁（CI 已生效，勿动）；
  - 消费断链：320 条锁版断言零执行（唯一消费者是存在性测试）；管理端评测平台 9 个数据集全 rag、cs=0；live 接线为 manifest 遗留的后续任务；
  - 首次全量离线回放（CSRouter.route 完整入口，零 LLM）：**intent 40.6% / target 52.8% / risk 63.4%**；失败样例暴露三类系统性口径问题（k_policy/k_faq 边界、域判定门槛 vs 自然表达、safety/multi_turn 断言口径）+ 数据集治理悬空（v2/ 304 条修订分片与锁版 320 并行未定案）；
  - 分工红线：工程正确性（幂等/资金/状态）归 119 个既有单测，评测集只管行为质量。
- **产物**：探针脚本持久化至 `scripts/cs_dataset_probe.py`（可复现基线）；本任务 TASK/PROGRESS 落稿。
- **同会话顺带产出**（关联但非本任务范围）：capability-governance G1–G6 全部合入关闭（#198/#199/#200/#202/#203/#204/#207/#211/#216）；owner 归属确认（单一团队，platform-team 即实值）；G5 翻正连带项移交 travel-v2 E5；客服域投产就绪度评估（结论见 TASK §2.4 关联上下文——frontend-cs 生产定义已存在、资金后端/压测/渗透测试为硬缺口、渠道=web 已确认）。
- **待办**：下一会话从 P1 开始（启动清单 TASK §8）。

### 关联：客服域投产检查清单（同日评估遗留，供投产会话使用）

1. frontend-cs 生产构建验证：`COMPOSE_PROJECT_NAME=agent docker compose -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.local.yml build frontend-cs && up -d frontend-cs`，验证 :3300/login 200——上一会话 build 被中断未完成（服务定义已在 local.yml:223，纯验证动作）；
2. 渠道决策落档：真实渠道=web（已确认）——建议同步 `docs/domains/customer-service.md` 渠道段；
3. 资金功能首期范围决策：不含资金则 CS_BUSINESS_GATEWAY_MODE/CS_REFUND_REQUEST_MODE 保持 disabled 并在投产检查记录；
4. 压测（多坐席容量）与安全渗透测试：公开全量投产前必做；
5. 共享库 govtest/probe 测试残留：确认非活跃后经管理端 DELETE 清理。
