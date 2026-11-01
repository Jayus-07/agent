# PROGRESS — cs-evaluation-upgrade（客服评测集升级）

- 任务文件：同目录 TASK.md（唯一事实源）；评估由上一会话完成，本文件承接实施进度
- 更新时间：2026-11-02
- 当前状态：**P1–P3 已完成合入，P4（offline）待实施、P5 机制待建；P4 live 跑批预算待用户拍板**

## 阶段状态总表

| 项 | 主题 | 状态 | 分支 / PR | 证据 |
|---|---|---|---|---|
| 评估 | 评测集满足度评估（资产/消费/接线三层 + 320 条回放基线） | **已完成**（上一会话，结论入 TASK §1） | — | scripts/cs_dataset_probe.py 可复现 |
| P1 | 离线回放 runner 入 CI + v2/ 分片定案 | **已完成**（2026-11-02） | feat/cs-eval-p1 / [PR #223](https://github.com/Jayus-07/agent/pull/223)（merged 63df24b） | 见日志 2026-11-02（一） |
| P2 | 数据集修订三类口径 + 升 v2.1 | **已完成**（2026-11-02） | feat/cs-eval-p2 / [PR #228](https://github.com/Jayus-07/agent/pull/228)（merged a729f3c） | 见日志 2026-11-02（二） |
| P3 | 词表/规则能力补强（cs-gate 护航） | **已完成**（2026-11-02，含候选 4 条收编升 v2.2） | feat/cs-eval-p3 / [PR #229](https://github.com/Jayus-07/agent/pull/229)（merged b770788） | 见日志 2026-11-02（三） |
| P2 | 数据集修订三类口径 + 升 v2.1 | 待实施（依赖 P1） | — | — |
| P3 | 词表/规则能力补强（cs-gate 护航） | 待实施（依赖 P1） | — | — |
| P4 | 管理端评测中心接线（offline 先行；live 需用户拍板预算） | 待实施 | — | — |
| P5 | 坏例回流运营闭环 | 长期 | — | — |

状态取值：待实施 / 进行中 / 待验收 / 已完成 / BLOCKED（注明原因）/ PAUSED（注明现场）。

每项完成后回填：分支与 PR、实际测试命令与 PASS/FAIL、基线数字对比、遗留事项。

## 环境

- Worktree：`D:\Program Files\workplace\agent-gov`（复用；P3 收尾时停靠 docs/cs-eval-p2p3-progress，新分支自最新 origin/main）
- 基线时点：origin/main@b770788（P3 合并后；并行会话 travel-v2/ci-debt 持续推进，开工时先 fetch 对齐）
- 测试解释器：`D:\Program Files\workplace\agent\.venv\Scripts\python.exe`（worktree 无 .venv，需从主仓库拷 .env）；真库 5433
- frontend-admin node_modules：junction 复用主仓库（如涉评测中心前端改动）
- 关键口径：回放链 CSRouter.route 完整入口；词表门禁 vocab_gate fail-closed 勿绕过；锁版修订流程见 datasets/README.md
- 环境坑：TASK §2.3（Mimosa hook / GitHub 抖动重试 / compose 等价原命令 / MSYS_NO_PATHCONV / connect_timeout / 并行会话纪律）——开工必读

## 日志

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
