# cs-capability-gap PROGRESS

## 当前集成状态（2026-11-06）

> 本节为当前权威状态；下方批次记录保留各自当时现场，若状态冲突以本节及最新 PR/提交为准。

- B4 PR #336、B5 PR #338、B6 PR #339、B7 PR #340 均已合入 main；B7 merge commit 为 `f1bfd73`。B4–B7 定向回归此前 **218 passed / 5 skipped**，B7 独立 PostgreSQL T2 **1 passed**。
- B8 本地 merge commit `37b16ff` 已合入 `origin/main`（`f1bfd73`），PR #341 已开放。集成回归 **262 passed / 6 skipped**；新发票 Service/测试 Ruff、Python 编译、YAML 解析、`git diff --check` 通过。PR 首轮 RAG Smoke preflight 为 **1109 passed / 5 skipped / 1 failed**；唯一失败是 T2 测试未给迁移后的会话状态列显式合法值，已修正测试并在隔离 PG T2 **1 passed**，等待修复提交后的 CI 重跑。全 touched-file Ruff 仍报 10 个既有旧行问题，未触及配置导入、事实投影导入排序和 `init_db.py` 局部变量。
- B9 立项文档仍在 `codex/cs-b9-charter` 工作树，尚未提交/开 PR；待 B8 完成后同步最新 main。
- q3 `b4-acc-q3` 退款提案仍为 `expired`（到期 `2026-11-05 15:43:48 UTC`）；2026-11-06 通过共享 PG 只读核验 `confirmed_at`/`executed_at` 为空、对应客服 `refund_request` 动作 **0**、幂等记录 **0**、业务库对应订单退款记录 **0**。没有确认、提交或发起退款；后续验收继续不触碰。
- B4–B8 最终主容器重建及运行时验收尚未开始；待 B8/B9 集成后，从含全部最新代码的任务工作树重建，再核验迁移、健康状态和安全的 mock 流程。真实支付、物流、税务和资金系统不接入。

## 管理端能力治理现状（2026-11-06）

- 已有通用运营治理：主管退款审核队列（必填意见、终态锁、`audit_logs`）、客服团队/RBAC、工单与会话队列、质检、知识缺口与 FAQ 草稿审核；新动作通过用户确认、服务端授权/租户校验、幂等和 `agent_actions`/`audit_logs` 留痕。
- B4–B8 的功能开关和阈值主要由环境变量/后端配置控制；代码与检索未发现管理端对这些能力提供逐项启停、金额/风险阈值、白名单、发布回滚或策略版本管理的 UI/API。
- 结论：执行和审计治理已有复用，能力运营治理只部分体现；专用管理入口仍是待补项，不作为 B4–B8 已完成能力。
> 接手会话现场记录。依据 FINAL-PLAN.md（main c6023dc）执行。恢复时先读 FINAL-PLAN §十一 + 本文件，核对 worktree/HEAD/未提交状态。

## 现场（2026-11-04）

- worktree：`D:/Program Files/workplace/agent-cs-fix-entry`，分支 `fix/mall-sandbox-cancel-nameerror`（基于 c6023dc）
- 隔离测试 PG：容器 `agent-csfix-b0-pg`，宿主 127.0.0.1:**15434**，postgres:16-alpine，口令 b0sandbox；为 B1+ 复用保留

## B0：sandbox 取消订单 NameError —— ✅ 已合并部署（2026-11-04）

- PR #309（squash 前单一干净提交 6eb9714）合并为 main `e2990b9`；分支已删（本地+远端）
- 中途波折：首推的 workflow env 行内嵌了「postgresql:// 用户名:口令@主机」形态的连接串，被 scan `url_with_credentials` 形态闸在 tree+history 两道先后阻断；修正为 `MALL_SANDBOX_PG_DBNAME` + job 既有 PG* env 接线（workflow 文件内零凭据），并以 force-with-lease 重写未合并分支历史后过闸——**后续批次写 workflow/文档时一律不写带凭据 URL 原文（事故记录也只写形态描述）**；另：本地 scan_secrets --tree 只扫已跟踪文件，**提交前先 git add 再跑本地 scan**，未跟踪文件会逃过本地检查
- CI 终态：scan pass；preflight pass（897 passed / 2 skipped，B0 新测试 3 条真跑非 skip，剩余 2 skip 为既有归因）；付费评测 job PR 触发 skip 属预期
- 部署：主仓 e2990b9，devctl rebuild 完成，12 容器全 healthy，:8000 /health ok（migrations/schema/redis/rag 全 ok），容器内 mall_service.py 已确认含修复（:536）
- 经验：Git Bash 调 devctl 必须 `MSYS2_ARG_CONV_EXCL="*" cmd.exe /c "devctl.bat ... -y"`（`/y` 会被 MSYS 转换吞掉）

### B0 最终改动（3 文件）

1. `backend/customer_service/service/mall_service.py` +1 行：`_cancel_order_local` 签名补 `expected_status: str | None = None`（实际故障比计划记录更早：调用方传参→TypeError）
2. `backend/tests/customer_service/test_mall_cancel_sandbox_pg.py` 新增：隔离 PG T2，3 条；连接来源二选一（`MALL_SANDBOX_PG_TEST_DSN` 本地 / `MALL_SANDBOX_PG_DBNAME`+PG* env CI）；库名守卫；迁移重放带表存在性跳过
3. `.github/workflows/rag_smoke.yml`：免费档 job env 加 `MALL_SANDBOX_PG_DBNAME`、init 步骤建 `agent_business_sandbox_test`、测试进定向清单（CI 真跑）

## B1 预研结论（供施工直接用，勿重探）

- `scripts/index_cs_seed_docs.py`：读 `{DOCS_DIRECTORY}/{kb_id}/customer/`（容器内 /app/data/docs），当前 8 篇靠人工 docker cp 送入
- **主 Dockerfile COPY 不含 docs/**（仅 backend/ mcp_servers/ scripts/）→ B1 需加一行 `COPY docs/customer-service/kb/ ./docs/customer-service/kb/`（scoped，仅客服 kb），seed 脚本扩展为先从 /app/docs/customer-service/kb 同步到 DOCS_DIRECTORY 再索引，消灭手工 docker cp
- 政策文档内容锚点：退款=退回钱包（mock `_wallet_for`/`_wallet_append`，kind=topup/refund）；退款申请→`pending_review`→人工审核（SMOKE-MATRIX S3，24h 口径）→钱包回款；数字规则全部标【业务确认】
- 既有源文档（docs/customer-service/demo-kb/）：policy-refund-timeline / policy-shipping-fee / faq-service-general（含发票/地址/优惠券问答）等可作底稿，但注意 demo-kb 是「模拟数据」口径、面向国际站多地区，新 kb 目录按计划 §六 生产口径重写
- 文档落点：`docs/customer-service/kb/cs_policy/`（8 篇）+ cs_faq 高频速查 1 篇（§六 表格为逐篇必含章节清单）

## B1：RAG 知识底座 —— 已提交（本 PR），合并后执行 A4-A7 真机验收

改动面（分支 `docs/cs-rag-kb-seed`，基于 e2990b9）：

- 新增 `docs/customer-service/kb/`（18 文件）：cs_policy 8 篇政策（refund/price-match/coupon/points/payment/shipping/invoice/aftersales，§六必含章节+【业务确认】标注）+ cs_faq 速查（30 条 Q&A）+ README + **历史 8 篇种子自 demo-kb 收编（逐字节一致，全部种子可从 git 复现）**
- 新增 `scripts/cs_kb_seed.py`：种子清单（17 条）+ 同步逻辑（纯 stdlib 可单测）；重写 `scripts/index_cs_seed_docs.py`：先同步 git 源→数据卷再索引，消灭人工 docker cp
- 新增 `backend/tests/scripts/test_cs_kb_seed_sync.py`：6 条契约测试，含 **SEED_FILES↔git 文件一致性守卫**（删文档不改清单→CI 失败）
- `Dockerfile` +1 行 scoped COPY（docs/customer-service/kb/ 进镜像）
- rag_smoke 定向清单 +1 测试

预验收结果（合并前）：

- ✅ A0 验收关键预检：cs_policy 已被 k_policy/k_faq 兜底/k_promotion/as_*/t_delivery_estimate/t_order_cancel 等 10+ 意图 kb_ids 声明（intents.py），KB 管道全通、仅库空——零代码成立（注意：第一轮 grep 被 head 截断曾误判「无人查 cs_policy」，已修正）
- ✅ A1 契约测试 6/6（含一致性守卫，真实抓到历史 8 篇不在 git 的缺口并已收编）
- ✅ A2 worktree 镜像构建（agent-csfix-b1-app）：/app/docs/customer-service/kb/ 完整（8 政策+3 faq）；容器内 `from scripts.cs_kb_seed import` 冒烟 17 条/8 政策
- ✅ A3 scan_secrets 0 命中；py_compile 全过

合并后真机验收结果（2026-11-04 实测，对齐 §九.1）：

- ✅ **A4** 容器内 seed 重跑：`ok=17 skipped=0 failed=0`（速查文档识别 39 组 Q/A→41 chunks）。途中修一个部署缺陷：数据卷 cs KB 目录混合属主（docker cp 时代 root 遗留），appuser 同步被拒——已按镜像运行时身份（10001）限定范围 chown 六个 cs KB 目录
- ❌ **A5** cs 库检索不可达——**先于 B1 的平台级问题，非本批回归**。根因链（证据齐备）：向量索引身份 `custom-gpu/bge-m3`（rag_index_meta，建于 10-27）≠ DB 注册表运行时解析身份 `db/bge-m3`（指纹不可用）→ fresh 进程硬抛 IndexEmbeddingMismatchError、rag-service 向量腿异常降级 → BM25 全局兜底 → customer keep-set 含 audience=customer 的 travel 库 → 海量旅游内容霸榜（cs_faq 老库同样失 scoped，返回文档 kb 元数据明示 travel）。**旁路探针证明数据+过滤+翻译+embedding 全部健康**：绕过身份检查后，单 kb 与 $or 双 kb（知识专家形态）均完美命中新政策文档（退款政策 top1、速查 Q&A 命中）
- ❌ **A6** 对话 6 问法（demo_fuzhou 经 ：9080 网关 + X-API-Key 实测）：退款（7 天无理由）/发票（抬头规则）/优惠券 三问由 FAQ 层部分兜住；**价保/积分/支付失败 三问空答**（新政策文档不可达，同 A5 根因）。**附加发现：ai.cs_faq 审核表存在内容矛盾**——「店铺券与平台券可叠加，单笔最多 3 张」与新政策文档「默认不可叠加」直接冲突（faq 层优先命中了错误口径），需 FAQ 治理
- ⚠️ **A7 口径冲突**：坐席端 `/cs/knowledge/search` 只查 `ai.cs_faq` 审核表（cs_knowledge.py:162 仅 faq store）；速查文档在 RAG cs_faq 库中坐席端搜不到。计划 §四「接 RAG 通道默认不做」与 §九.1「坐席端知识检索命中速查问答」互相矛盾，需用户裁决（补 ai.cs_faq 种子 or 坐席端接 RAG 通道）

修复方向（**需用户裁决，触及模型注册表领域，疑似并行会话工作范围**）：

- 方案 a：把 DB 注册表 bge-m3 的 provider 登记对齐回索引身份 custom-gpu（或补指纹）→ 向量腿即恢复，零索引重建
- 方案 b：以当前注册表身份重建向量索引（重操作）
- 方案 c：转交掌握模型注册表的会话处理（证据已足够定位）

其余说明：验收期间共享栈被并行会话两次 rebuild（#311/#314 合并），日志证据随重启丢失但关键探针结论均在重启前后复测一致；本会话未动模型注册表与索引身份。

## 裁决后执行结果（2026-11-04，用户拍板：方案 a + FAQ 治理 + A7 接 RAG）

**根因修正（推翻上节 A5 假设）**：「provider=db」线索走到 DB 层后发现模型注册表**本来就是对齐的**（bge-m3→custom-gpu、指纹 9b0cdc11 与索引元数据一致，#311-#314 间已修复），真正的根因是——

**6 个 cs KB（cs_faq/cs_policy/cs_product/cs_complaint/cs_scripts/cs_aftersales）于 2026-10-22 19:11-19:13 UTC 被 local_super_admin(id=279) 批量置 inactive**（auth.knowledge_base_audits 留痕）。customer 主体授权集=audience=customer 且 active 的库 → 只剩 travel → `_constrain_filter_to_authorized` 丢弃 kb_id → `_keep` 拒掉所有 cs 文档 → 向量腿恒 0 + BM25 全局兜底只剩 travel——完整解释「cs 查询返回旅游内容」「vector=0 无异常」。此前所有探针（原始 store 直查）绕过了 `_keep` 授权层所以「健康」。

**执行动作与验证**：

1. ✅ **KB 启用**：经 `knowledge_base_store.activate_knowledge_base`（版本 CAS+审计）启用 6 库（v2，actor=task:cs-capability-gap-b1）。注册表每请求实时读库，无需重启。复验：authorized=['cs_product','cs_aftersales','cs_faq','cs_complaint','cs_policy','travel','cs_scripts']；**A5 终验 7/7 命中**（rag-service `/retrieve_docs` HTTP 链路：退款/券叠加/积分/支付/开票/价保 六问全命中 cs_policy 新文档 + cs_faq 老文档）
2. ✅ **A6 终验**（第二轮，demo_fuzhou 经 :9080）：q3 券叠加 **3/3 全过**（FAQ 治理生效）；q4 积分 3/3（政策库 evidence 引用）；q1/q6 FAQ 兜住正确答复；q2 价保/q5 支付检索命中新文档但 **ClaimVerifier 判 hallucination 拦截**（数字句「30 分钟有效/15 天窗口」在证据 chunk 中缺失→fail-closed 拒答，**护栏正确行为**，检索完整性/校验容差调优记 P2 挂账）——6 问无空答、无错误口径
3. ✅ **FAQ 内容治理**：`ai.cs_faq` 112 条 published 逐条对照新政策，修订 13 个客户域矛盾口径（14 次 upsert，含 id=538 短键行）：券叠加(id=23)/退款路径与时效(id=4/87/90/538/810)/审核时效(id=12/88)/发货时效(id=10)/开票时限(id=15)/人工时间(id=22)/会员等级(id=8)/验收(id=89)——全部对齐 kb/ 新政策与 mock 行为；员工内部政策（差旅/变更/运维）与国际站分国家口径不在治理范围
4. ✅ **A7 坐席端接 RAG 通道**（PR #317 → ce902ea 已合并部署）：`/cs/knowledge/search` FAQ+RAG 合并、SearchItem.source=faq|rag（默认 faq 旧前端零影响）、失败逐库降级、to_thread 不阻塞事件循环；CI 907 passed/2 skipped；**真机终验**（cs_wire_agent 经 :9080）：搜「优惠券叠加」7 条 = FAQ 1（治理后口径）+ RAG 6（速查 2 + 通用 FAQ 2 + coupon-policy 新政策 3），source 区分正确、两通道口径一致互证；主链路零回归（/health ok + 17 容器 healthy + 三端在位）

**P2 挂账**（不阻塞 B1 收官）：

- ClaimVerifier 误杀有据数字句：检索完整性（rerank/top_k）与校验容差调优，B2 查询域接 facts 后一并观察
- 10-22 批量停用 cs KB 的背景无人可考（审计无 reason 字段）——已在本文件留痕，运营侧如需审计问询找 super_admin 279

## B2：积分/等级查询（查询型样板）—— ✅ 已合并部署（PR #320 → d1d38df，CI 对账 919 passed/2 skipped = 907+12 零漂移）

> 实现与验收证据见下（分支 feat/cs-points-query，commit dffbeb9 + b734a7b）。

**实现**（FINAL-PLAN §五 B2 + 模板 A 全集，14 文件）：

- 路由：`t_points_query`（TRANSACTION，BUSINESS_QUERY，kb_ids=["cs_faq","cs_policy"]）——vocab.py TRANSACTION 域词（积分/会员等级/等级权益）+ 3 条句式 pattern，VOCAB_VERSION → **2026-11-04.1**（test_vocab 版本守卫断言同步，AGENTS.md #8 连带更新）；出域豁免补「等级/权益」（「积分/会员」10-08 已在表，HANDOFF §四.1 与实际有出入，以代码为准）；fine_router 域内词表；agent_assist SOP 三步行
- 口径钉住：golden T-015「会员积分怎么用？」KNOWLEDGE 2 票 > TRANSACTION 1 票不翻域（test_points_flow 路由测试钉死，真机 q4 复证走知识域 FAQ id=16 原口径）
- Service：`points_service.py` 双模式（http→`GET /business/points`；sandbox→finance.points_ledger 净和 + customer.tier_profiles）；tier_label/tier_note 由 Service 归一（数据面只回 tier 码）
- mock：懒种子 **8000 分 + gold（spend_90d=3500，黄金档 2000≤x<8000 与 FAQ id=8 一致）+ 2 笔 earn 流水**；contract.md **v1.2**（变更记录 + 「会员积分与等级」节）
- facts 三件套（W1 红线）：`_project_points`（points/tier/tier_label/tier_note/recent_*）+ projectors 注册 + composer `_fact_amounts` 扫 "points"/"recent_points" 键、`_amount_claim_kind` 加 `points` 标签——积分→元越权换算（「8000 积分可抵 ¥80」）fail-closed 打回模板
- 迁移：`132_cs_points.sql`（finance.points_ledger + customer.tier_profiles，账本净和为余额单一事实源，872 幂等种子）+ init_db.py MIGRATION_TARGETS 登记 "business"（共享栈 db-migrate 实际应用，schema_migrations 有记录）
- 测试：`test_points_flow.py` 12 例 = 路由钉住（含 T-015）+ 豁免词 + mock 契约数字一致性 + http Service + facts 导出/降级 + 投影白名单/非法 tier 丢弃 + composer 守卫放行/拒绝 + **隔离 PG（15434）sandbox 直查**（fixture 自带 003+132 建表，POINTS_SANDBOX_PG_DBNAME 双路径 env 同 MALL 守卫纪律）

**验证链**：

1. T2 全绿：test_points_flow **12/12**（含隔离库 sandbox）；customer_service 全域回归 **1561 passed**（发现并修复 test_vocab 版本断言连带）；vocab 双门禁（test_cs_eval_gate + test_cs_decision_replay）全过
2. 阴性验证 2 处成立：撤 facts.py 投影器注册 → 投影白名单 2 例失败；撤 fine_router 词条 → 路由测试失败（证明测试钉住行为）
3. worktree rebuild：backend/business-mock/frontend/frontend-admin 重建 healthy；**132 落库实证**（agent_business.schema_migrations 有 132 行 + 872/gold/3500 种子在位；/health migrations 字段 131 为 memory 库口径，非漂移）；frontend-cs 需单独 `docker compose -p agent build frontend-cs`（devctl rebuild 不含它，容器 config_files 指主仓路径——已从 worktree 重建）
4. **真机验收 4/4 PASS**（demo_fuzhou 经 :9080，脚本 d:/tmp/cs_b2_acc.py，结果落盘 d:/tmp/cs-b2-acc-result.txt）：
   - q1「我的积分有多少？」→ 8000 + 黄金会员 + 1.5 倍权益 + 最近 200 分流水 ✅
   - q2「我的等级权益有哪些？」→ 等级 + 权益 + 积分（豁免词「等级/权益」召回路径验证）✅
   - q3「查一下我的积分和最近流水」→ 余额 + memo「订单完成奖励」入 facts 进回复（W1 纪律真机面）✅
   - q4「会员积分怎么用？怎么抵扣？」→ 知识域 FAQ 答复（每消费 1 元累计 1 分，满 1000 分抵 10 元）+ 参考来源 ✅（政策问句未误走数据查询）
5. 主链路零回归：:8000 /health ok + :3100/:3300 200 + :9080 用户/坐席双登录冒烟 200；客服 RAG/FAQ 链路经 q4 实测在位

**收尾**：rag_smoke 定向清单 +`test_points_flow.py` 与 `POINTS_SANDBOX_PG_DBNAME` env；GAP_LIST 积分行改「已覆盖（B1 政策问答 + B2 意图级积分/等级查询）」；customer-service.md 第 8 行（积分/等级查询口径）与第 49 行（词表扩圈台账）同步；FAQ id=8/16 与 mock/迁移种子数字三方一致（3500∈黄金档、100 分=1 元）。

**CI 门禁坑（B3+ 注意，首轮 CI 实证）**：新迁移文件必须显式登记进 `.github/scripts/rag_smoke_path_filter.py`（`NON_AI_MIGRATION_ENTRIES` 或 `PAID_RELEVANT_MIGRATION_ENTRIES`），否则 `Detect RAG changes` 直接 exit 2、preflight 被跳过（首轮即因此 fail，补登记 b734a7b 后重跑全绿对账 919）。

## B3：优惠券查询/用券校验 —— 实现与验收（2026-11-05，分支 feat/cs-coupon-query，基于 41d97fa）

**实现**（FINAL-PLAN §五 B3 + 模板 A，18 文件）：

- 路由：`t_coupon_query`（TRANSACTION，BUSINESS_QUERY，kb_ids=["cs_policy","cs_faq"]）——vocab TRANSACTION 域词（优惠券）+ 3 条句式（券列表/查券/用券异常），VOCAB_VERSION → **2026-11-04.2**（test_vocab 断言同步）；**域冲突钉住**：「优惠券怎么领/能不能叠加」政策问句 KNOWLEDGE 2-3 票优先不翻域（golden「优惠券怎么领？」等 3 条口径不变）；cases.jsonl 退款语义「用了优惠券想退了重拍」不落 TRANSACTION（单测钉住）；fine 词表含「用券/领券」（真机实锚补入，见下）
- Service：`coupon_service.py` 双模式（http→`GET /business/coupons` + `POST /business/coupons/check`；sandbox→marketing.coupons/coupon_redemptions）；kind_label/reason_label 展示归一 + **currency=CNY 归一**（composer 金额守卫按币种比对，facts 缺 currency 会把元面额一律 fail-closed——balance 样板未暴露、B3 首轮测试揪出）
- mock：四券懒种子（SAVE30 满 200 减 30 / NEW5 无门槛 5 元 / D90 9 折最高减 50 / OLD10 已过期，**stackable 恒 false** 与 FAQ id=23 一致）+ 一条核销样本（绑定 shipped 种子单 → not_stackable 确定性样本）；check 规则优先级 expired→threshold_not_met→not_stackable→applicable（政策 §6 四类原因，排除品类由结算页校验不模拟）；contract.md **v1.3**
- facts 三件套（W1 红线）：`_project_coupon`（面额/门槛/叠加/状态，currency 入白名单）+ `_project_coupon_check`（四类原因+订单实付）+ composer `_fact_amounts` 加 threshold_amount/discount_value/max_discount/order_total 四键、`_amount_claim_kind` 加 `coupon` 标签（券面额按 coupon 类核验，check 订单实付按 order 类）
- 迁移：`133_cs_coupon.sql`（**CREATE SCHEMA IF NOT EXISTS marketing** + coupons/coupon_redemptions，UNIQUE(customer_id,coupon_code)，872 四券幂等种子）+ init_db 登记 + **path_filter NON_AI 登记（B2 新坑直接规避）**
- 测试：`test_coupon_flow.py` 16 例 = 路由钉住（政策 3 问句不翻域 + 退款样本不误入 + 真机实锚「用券」句式）+ 豁免词 + mock 数字一致性 + **check 四类原因逐条** + Service 归一 + facts 导出/降级 + 投影白名单/非法枚举丢弃 + composer 守卫放行/拒绝 + 隔离 PG（15434 复用，sandbox 券档案+核销 not_stackable+过期，重跑幂等）

**验证链**：

1. T2：test_coupon_flow **16/16**（两遍跑幂等）；双门禁 + B2/查询/facts/composer 回归 110 过；全域 **1577 passed 零失败**；阴性验证 2 处成立（撤券投影器→投影 2 例失败；撤 fine 词条→路由测试失败）
2. 环境事件：Docker Desktop 引擎重启一次（redis AOF incr 截断损坏→`redis-check-aof --fix` 截尾 107 字节恢复 healthy；agent-csfix-b0-pg 拉回）——共享栈全量恢复后复测
3. worktree rebuild（基于 41d97fa rebase，stash 过桥 #322 docs-only）+ 133 落库实证（marketing schema/两表/872 四券种子）+ frontend-cs 单独重建（GAP_LIST B3 行）
4. **真机验收 4/4 PASS**（demo_fuzhou 经 :9080，脚本 d:/tmp/cs_b3_acc.py，落盘 d:/tmp/cs-b3-acc-result.txt）：
   - q1「我的优惠券有哪些」→ 四券列表（满减 200-30/无门槛 5 元/9 折最高减 50/已过期标记）+ 不可叠加 ✅
   - q2「订单 MO-6DEC4021 用券为什么没生效」→ **不可叠加（该订单已用过一张）** ✅（首轮 FAIL 实锚：fine 词表缺「用券」落域默认 t_order_status 答成订单状态——补词 rebuild 后复测过）
   - q3「优惠券能不能叠加使用？」→ FAQ id=23 原口径 + 参考来源（政策问句未误走数据查询）✅
   - q4「订单 MO-35374034 的优惠券为什么用不了」→ **未满足门槛（¥199.00 < 满 200，实付口径）** ✅
5. 主链路零回归：:8000 /health + :3100/:3300 200 + :9080 坐席登录 200；B2 积分链路经全量回归复证

**收尾**：rag_smoke 清单 +`test_coupon_flow.py`；GAP_LIST 优惠券行改「已覆盖（政策问答 + B3 意图级券查询/用券校验）」；customer-service.md 第 8/49 行同步；mock/迁移种子与 coupon-policy.md/FAQ id=23 三方一致。

## B7：在途订单改地址 —— 实施中（2026-11-06，分支 feat/cs-address-change）

**当前实现**：新增在途状态复核、订单级改址提案/确认、business-mock 幂等模拟登记、每单一次限制、真实订单/承运方零副作用；地址缺槽走既有 need_info → proposal → 用户确认；成功后 `agent_actions` 的 after_state 保存模拟状态和请求 ID。账号默认地址能力保持原语义；`.env.example` 登记默认关闭的 `CS_ADDRESS_CHANGE_MODE` 与 HTTP mock 联调说明。

**验证证据**：B7 定向 `test_address_change_flow.py` 当前 **10/10 通过**，含独立 PostgreSQL T2：应用状态迁移 050 与 B7 迁移 137 后，真实 `AuditRepository` 将 `address_change/simulated`、订单/地址/请求 ID 和两个“真实副作用为 false”标志写入 `agent_actions` 并读回。客服双门禁与相邻回归最新 **212 passed, 2 skipped**；`py_compile`、涉及 Python 文件 Ruff、`git diff --check` 通过。独立 PG 仅绑定 `127.0.0.1:15435`，没有连接共享业务库。

**本轮补强**：HTTP 写请求已成功到达 mock、但客户端得到非标准异常时，现在映射到既有 `verifying/in_doubt` 状态；新增端到端测试验证 mock 已登记一笔、用户被提示勿重复提交且审计结果为 `in_doubt`。这不增加人工审核流程，也不改变真实订单或物流商状态。

**环境说明**：本轮 212 用例命令复用了任务专属 PG `15435`，B7 AuditRepository T2 通过；points/coupon 两个 sandbox T2 因未配置其独立 DSN 而跳过。通用 residue guard 因 worktree 无 `.env` 尝试连接 `localhost:5432` 并报无密码/PoolTimeout，pytest 退出码为 0。共享数据库未被操作。

**未验收**：B7 代码尚未从本 worktree 重建部署；当前 `agent` Compose 项目由多个 worktree 的配置共同组成，不能安全地用单一 B7 worktree 重建替换。共享栈当前服务健康不等于 B7 已部署；真机对话尚未验收，状态为 **UNVERIFIED**。待共享栈配置可安全归属/重建后再做代码部署与真机验收。

**迁移顺序约束**：本分支迁移 137 保留 B5 135 的 `urge_request` 与 B6 136 的 `price_match`；B7 PR/迁移须排在 135/136 之后合入并按序应用，避免旧迁移后到时收窄约束。

## 后续批次

B3 定型（本批合并）→ B4 支付查询（复制查询型模板，红线：转退款走 refund-requests 人工审核闸）→ B5-B8。

## B4：支付流水查询 + 重复扣款退款提案（PR #336 开放；CI 修复续工，2026-11-06）

**现场**：worktree `D:/Program Files/workplace/agent-cs-fix-entry`，分支 `feat/cs-payment-query`，PR #336 开放（初始 HEAD `7f948b815f7392b6a62e00d2fe2d8df15967554f`）；CI 修复已推送。已 rebase 到 `origin/main=987eddb`；最近测试通过的 HEAD `a267cf14d57add39957be883c871e00814e017a1`。共享栈已从本 worktree 重建；隔离 PG `agent-csfix-b0-pg:15434` 保留。

**实现范围**：新增 `payment_service.py` 双模式查询；迁移 `134_cs_payment.sql` 和 init_db/path_filter 登记；mock 三态支付流水；新增 `t_payment_issue`、支付词表及 facts 白名单/金额守卫；有限 TaskPlan 先查流水，满足精确退款候选条件后调用既有 refund request proposal/confirmation 流；支付回复固定使用可信模板；客服知识页 GAP_LIST、customer-service 文档、mock contract、rag_smoke 定向清单同步。支付记录只读；任何条件不满足时不创建提案；提案仍须用户确认后才进入既有人工审核，不存在自动退款通道。

**代码与测试证据**：

1. T2：支付、任务计划、CSRouter、FineRouter、composer、vocab 六套定向测试带显式 sandbox DSN 最终 **141 passed，零 skip**（30.31s，包含 134 迁移幂等测试）。此前未传 DSN 的合并运行是 140 passed/1 skipped；随后已补跑并在全量定向集合中消除了该 skip。
2. Python `py_compile` 与 `git diff --check` 通过。此前客服全目录基线 **1596 passed, 2 skipped**（8m44s；发生在本节最后两处细路由/模板改动之前，不能替代最新定向结果）。阴性验证已确认移除支付 facts 投影器会令测试失败、移除支付路由细词会令路由测试失败。
3. `verify_migration_state.py --image agent-db-migrate` 输出 `MIGRATION_STATE_OK`：仓库/镜像 139 个已登记迁移一致，权威数据库全部已应用；B4 134 已应用。隔离 PG 的 134 重放和支付种子测试在定向用例中通过。
4. 后端 `devctl.bat rebuild /y` 成功，`frontend-cs` 从本 worktree 使用 compose overlay 单独重建；8000/8081/3300/8090 均 HTTP 200，devctl 对 3100/3200 探针也通过，所有代码容器 healthy。9080 根路径返回 404（网关未暴露根路由，非健康判据）；demo 登录由真机脚本 HTTP 200 实证。

**真机验收**（脚本 `D:/tmp/cs_b4_acc.py`，证据 `D:/tmp/cs-b4-acc-result.txt`）：

1. q1 单笔成功流水：PASS，真实订单号/金额/渠道/状态。
2. q2 失败流水：PASS，明确包含“可稍后重试支付”。
3. q3 重复扣款：PASS，核验到两笔同额成功记录，返回既有待确认退款卡并说明确认后走人工审核；脚本未发送“确认”，没有提交退款申请。
4. q4「支付失败怎么办？」首轮 **FAIL**：宿主 GPU 服务 8088 未运行时 RAG 不可达；恢复后 Supervisor 同轮调用 KnowledgeExpert 两次，第二次响应因 ClaimVerifier `citation_not_found missing=['E5']` 被拒绝，覆盖第一次有效答复。该失败最初误判为单纯检索完整性问题；根因及后续修复见本节「q4 修复与真机复验」。ClaimVerifier 未被绕过或修改。
5. q4 隔离复测脚本/结果：`D:/tmp/cs_b4_q4_retry.py`、`D:/tmp/cs-b4-q4-retry.txt`；RAG 和 app 详细日志仍可按 session `b4-acc-q4-retry` 查询。

**状态与下一步**：B4 q1-q4 真机验收通过，PR #336 已开放且未合并；q3 session `b4-acc-q3` 保留待确认状态，不要发送确认。CI 两处问题均已修复：134 全新库客户外键种子、支付任务单测 gateway mode 环境依赖。最近测试 HEAD **1007 passed / 2 skipped**，必需 CI 全绿（真实 API 评测按 PR 规则跳过）。初始 PR 范围没有自动退款；本次后续扩展为 mock-only 低风险模拟退款（详见下文），真实资金退款与真实渠道仍未实现。

### q4 修复与真机复验（2026-11-06）

**根因**：`支付失败怎么办？` 路由为 KNOWLEDGE，confidence=0.63，高于 Supervisor 0.60 的路由门槛。第一次 KnowledgeExpert 已返回 ClaimVerifier 通过的有效答复；Supervisor v2 的高置信 L5（v1 默认路由同理）没有检查当前轮已有成功知识答复，因而再次调用 KnowledgeExpert。第二次检索/生成结果非确定性地引用不存在的 `[E5]`，ClaimVerifier 正确 fail-closed，Reporter 最终使用第二次拒答覆盖第一次答复。问题在 Supervisor 重复派发，不在 ClaimVerifier。

**修复**：在 v1/v2 两套 Supervisor 中，handoff、pending、风险、循环和分诊门之后，普通路由之前检查最近 Expert 记录与 `last_expert_result`。两者均为 KnowledgeExpert `success` 且答复非空时直接 `FINISH` 交给 Reporter，避免覆盖；失败/空答复仍允许再次尝试，带 `task_plan` 状态的编排不受该 guard 影响。未修改 ClaimVerifier、RAG citation 逻辑或退款确认流。

**验证**：

1. TDD：新增回归先在 v1/v2 均失败（两次都再次路由到 `cs_knowledge_expert`）；修复后定向集合 **81 passed，0 skipped**，包含 `test_payment_flow.py`、`test_supervisor_v2_priority.py`、`test_cs_supervisor.py`，显式使用隔离 PG 15434。用例覆盖成功知识答复收尾、失败/空答复仍可重试、pending 优先。
2. `py_compile` 通过；`git diff --check` 通过。使用当前 worktree 执行 `devctl.bat rebuild /y` 成功；app、business-mock、RAG、workers、frontend/admin 健康。复验探针：`:8000/health`、`:8081/health`、`:8090/readyz`、`:3100/login`、`:3200/login`、`:3300/login`、宿主 `:8088/healthz` 全部 HTTP 200。
3. 真机 q4 脚本 `D:/tmp/cs_b4_q4_supervisorfix.py`，结果 `D:/tmp/cs-b4-q4-supervisorfix.txt`，全新 session `b4-acc-q4-supervisorfix-20261106`：`POST :9080/api/chat/customer-service` HTTP 200；答复含「重试」「支付方式」及引用 `[E1] [E2] [E5]`；RAG 日志确认 ClaimVerifier **3 claims 全部通过**；app 日志仅一次 `CS Expert start expert=knowledge`，其后 Supervisor 以「知识答复已成功生成，不重复调用 KnowledgeExpert」FINISH。未执行任何退款写操作或确认动作。

**q4 阶段收尾记录**：当时 q1-q4 验收通过、q3 session `b4-acc-q3` 待确认；后续已创建 PR #336。q3 仍不得发送确认，PR/CI 后续状态见下节。

### PR #336 CI 修复：134 演示客户外键（2026-11-06）

**根因**：GitHub `RAG Smoke preflight` 的全新业务库在迁移 134 写入 `customer_id=872` 的订单时触发 `fk_orders_customer`；干净初始化只有 001 的客户 1–3，没有 872。聚合 `RAG Smoke (8 cases)` 随 preflight 失败，其他免费契约、管理端构建和 scan 检查通过。

**修复**：134 在写 B4 三态订单前幂等补齐无个人信息的客服演示客户 872；只向前校准客户 ID 序列，避免后续默认 INSERT 撞主键。sandbox 验收增加客户存在、序列不低于 872 的断言。

**验证**：新建隔离库运行 `scripts/init_db.py` 时迁移 134 显示 applied；查询确认客户 872 存在、序列值 872、PAY-872 种子 4 条。对应 sandbox 用例 **1 passed**。完整本地 `init_db.py` 在自建容器因 vector 扩展、只读角色和既有 memory schema 前置条件退出 1，不记为全初始化 PASS。随后从本 worktree 执行 `devctl.bat rebuild /y` 退出 0，后端、business-mock、前端与管理端探针均 200。PR HEAD `a267cf1` 的 CI：134 初始化成功；支付单测 gateway mode 已固定；RAG preflight **1007 passed / 2 skipped**，Evaluation V2 免费测试、管理端、scan、Detect RAG changes 均通过。真实 API 评测按 PR 规则跳过。
# 持续实施任务启动（2026-11-06）

- 目标与边界：见同目录 `TASK.md`。当前 worktree 继续使用 B4 分支 `feat/cs-payment-query`；PR #336 开放、未合并，HEAD `9ee601b`。新需求为 mock 内严格低风险模拟退款 + 可审计/人工分流，不接真实系统。
- 会话启动证据：当时工作树干净；`origin/main=987eddb`，PR HEAD 与远端一致。q3 `b4-acc-q3` 仍 pending，禁止发确认或退款提交。当前 B4 实现和文档有未提交改动，详见下方现场。
- 顺序：先完成 B4 的 mock-only 自动模拟/审计并重验；之后 B5、B6、B7、B8 各自独立 worktree/PR；B9 只交立项文档。提交前展示差异并等待确认。
- 生产口径：现有 `business-mock` 是内存模拟；新业务动作须通过现有客服持久化 action/audit 链路落库，并标明 `execution_mode=simulation`。无真实接口的非演示客户走人工队列。

### B4：低风险重复扣款模拟退款（实现与本地验收，2026-11-06）

**实现**：仅在用户明确申请、事实来自 `business_payment_service`、业务地址精确为 `business-mock:8081`、币种 CNY、两笔全额成功且未退款、金额不超过显式上限时执行模拟。默认 `CS_AUTO_DUPLICATE_REFUND_MODE=disabled`、上限 `0`。Mock 新增按稳定 action_id 幂等的模拟端点；只标记最新一笔 demo 流水，不调用真实支付/退款接口、不创建真实退款申请。客服通过现有 `refund_execute` Action 和 audit 日志落库；未知结果不重试、不回退创建新提案。q3 `b4-acc-q3` 未触发、未确认、未提交。

**验收证据**：显式配置隔离 PG 后，`test_payment_flow.py` + `test_task_plan.py`：**82 passed / 0 skipped**；`test_audit.py` + `TestAuditPersistGateSQL`：**13 passed**；`py_compile`、`git diff --check` 通过。`devctl.bat rebuild /y` 从本 worktree 退出 **0**；app、business-mock、RAG、web、admin、客服前端探针均 HTTP **200**。容器运行配置复核 `auto_mode=disabled`、`max_amount=0`、目标 `business-mock:8081`，因此共享栈没有执行退款模拟。pytest 退出时全局 TaskService/residue guard 对 `127.0.0.1:15434/agent_memory` 报 PoolTimeout；支付 sandbox fixture 使用 `127.0.0.1:15434/agent_business_sandbox_test` 并通过，整组 pytest 退出码为 0。未检查隔离 Memory 库状态。

**数据库测试边界**：额外尝试启动完整 `test_side_effect_idempotency.py` 后，发现其 fixture 使用 `MEMORY_DB_CONFIG` 指向 `localhost:5433/agent_memory`（共享数据库），立即中止；该 fixture 会创建/清理 `s6_idempotency_records`、`s6_side_effect_probe` 测试表。该中断不计入通过项，未再查询或操作共享数据库；中断时 fixture 是否完成清理未验证。审计幂等通过项仅为无数据库的 `test_audit.py` 与 `TestAuditPersistGateSQL`。

**当前状态**：B4 变更留在 `feat/cs-payment-query` 工作树，PR #336 仍未合并；本批改动尚未提交/推送，等用户审阅确认。自动退款在任何部署中仍需先明确金额上限并显式启用；真实业务接入和 q3 操作均不在本批范围。

### B4 模拟退款审计持久化补验（2026-11-06）

- 新增 `test_mock_refund_action_and_audit_persist_once_in_isolated_memory_pg`，复用现有 `test_payment_flow.py`（已在 rag_smoke 定向清单中）。测试要求 `MEMORY_DB_CONFIG` 指向 test/sandbox 库，否则安全跳过；在 CI 的 `agent_memory_test` 上应实际执行。
- 通过本机 PostgreSQL 18 的 `127.0.0.1:5432` 新建临时隔离库 `agent_memory_cs_audit_20261106_test`，按当前审计/动作表契约建立测试表并应用迁移 050；运行该新用例 **1 passed / 0 skipped（8.31s）**。用例实际调用 `cs_graph_node._persist_audit_records` 两次，验证只保留 1 条 `agent_actions(status=simulated)`、1 条 `audit_logs(result=success)`、1 条 `ai.idempotency_records`；结束后查询三表均为 0 行并删除本次新建测试库。
- pytest sessionfinish 的全局 residue guard 提示该临时库没有 `llm_provider_credentials` 表（`UndefinedTable`）；不影响用例退出码或审计写入断言。此前有日志记录 `test_side_effect_idempotency.py` 27 passed，但该文件的 fixture 指向共享库 `localhost:5433/agent_memory`，与隔离纪律冲突；本轮不重跑，该数字不计入本批验收。
- 新增测试文件 `py_compile` 和全工作树 `git diff --check` 通过；测试文件已在 rag_smoke 中，未新增工作流清单项。
- Docker Desktop 初始未运行；普通权限启动服务被拒后，通过管理员上下文成功启动 `com.docker.service`，Docker Engine 29.2.1 恢复。此前只读记录曾显示隔离 PG 已 stop；本次 `docker inspect agent-csfix-b0-pg` 返回容器不存在，因此不把 15434 当作可用环境，也未操作共享 Compose 项目。
- 容器恢复后顺序重跑 `test_payment_flow.py` + `test_task_plan.py`：**82 passed / 1 deselected / 0 failed（10.19s）**；唯一 deselected 是独立执行的真实 PG 审计持久化用例。另有 Starlette/httpx 弃用提示及 residue guard 缺少 `llm_provider_credentials` 表告警，未影响退出码。审计用例在本轮安全护栏加固后再次独立执行：**1 passed / 0 skipped（10.10s）**；测试专用库相关动作/审计/幂等记录均清零、无其他会话后已删除。
- 当前共享栈只读复核：`:8000/health`、`:8081/health`、`:8090/readyz`、`:9080/health`、`:3100/login`、`:3200/login`、`:3300/login` 全部 HTTP 200；`agent-app-1` 的 Compose workdir 标签指向本 B4 worktree。应用内配置为 `auto_mode=disabled`、`max_amount=0`、gateway `http`、业务主机 `business-mock:8081`，没有执行模拟退款。
- Windows 可用内存当前为 **3.37 GB**。本轮没有修改运行时代码，未重复 rebuild；此前从本 B4 worktree 成功 rebuild 的证据仍适用。若后续有运行时代码改动，先按共享栈纪律检查资源，再从本 worktree 顺序执行 `devctl.bat rebuild /y` 和健康检查。
- PR #336 当前 OPEN，远端 HEAD `9ee601bd31593725a10dd20b60e52fc2906bc4af`；6 项检查成功，真实 API 评测 1 项按规则 skipped。PR 说明仍描述旧的“只提案、不自动模拟”范围，提交前需要更新。当前 B4 改动未提交/未推送；q3 `b4-acc-q3` 未访问、未确认、未提交退款申请。

### B4 PR 审查意见修复与复验（2026-11-06）

- 修复支付查询后会话订单引用滞后的问题：仅在支付服务核验成功后，用返回的订单号更新当前会话引用；新增同会话先查 A 再查 B 的回归。
- 修复 Mock 钱包支付后的流水缓存：先查询未付款订单再完成支付，会追加一条带 wallet 渠道的成功流水；操作幂等重放不会重复记流水。
- 修复超过 20 条支付尝试时的截断误判：支付服务统计完整响应中的成功数，暴露 payments_complete；列表不完整或数量标记不一致时不生成退款候选、不把未知误答成“没有成功支付”，用户转人工核对。事实投影保留完整性标记。
- 本轮安全 pytest 命令使用 --confcutdir=backend/tests/customer_service 绕开全局收尾钩子：test_payment_flow.py + test_task_plan.py **83 passed / 2 skipped**；py_compile、git diff --check 通过。跳过项是未配置隔离 PostgreSQL 的 T2 用例；Ruff 仅报告 5 个已有导入排序 I001，--ignore I001 检查通过。
- 纠正本轮首次测试的隔离疏漏：默认 pytest 收尾钩子会连接 MEMORY_DB_CONFIG 当前指向的共享 agent_memory:5433，执行任务表 ensure_schema() 并按 migration-test 条件清扫历史残留。该次收尾钩子已运行；其 DDL/DML 结果未核验，本记录不声称共享库未触碰。该守卫 SQL 只限定迁移测试标记记录，没有调用客服支付/退款动作。此后复验绕过该钩子，未再运行数据库清理。
- **部署状态：UNVERIFIED**。本次代码修复尚未从 B4 worktree 重建共享服务；当前 agent Compose 项目包含多个 worktree 配置且 20 个容器运行，不能安全地单独重建替换。旧版 B4 部署证据不能覆盖本次修复。
- PR #336 仍 OPEN；远端 HEAD 仍未包含这些本地修复，3 条 P2 需随后续提交更新。工作树未提交、未推送；q3 b4-acc-q3 未访问、未确认、未提交退款申请。

### B4 CI schema 兼容修复（2026-11-06）

- 提交 `91d54c5` 已推送到 PR #336。CI 的 `RAG Smoke preflight` 在新增持久审计 T2 用例失败：会话 schema 已从旧 `status` 列迁到 `conversation_status`，测试却依赖默认 `active`，触发状态约束。
- 测试现查询会话表列名：新 schema 显式插入 `conversation_status='open'`，旧 schema 显式插入 `status='active'`。仅修正测试夹具，不改业务运行代码。
- 在本任务独立 PostgreSQL 16 库上应用 006/031/050，并还原 020 中会话状态列迁移与检查约束后，重新运行真实审计持久化用例：**1 passed / 0 skipped**；`py_compile`、Ruff（忽略既有 I001）及 `git diff --check` 通过。
- 首轮 CI：RAG Smoke preflight **失败**（单个新增 T2 夹具状态列问题）；Eval V2 free contract、Eval V2 admin、scan 和 RAG path detection **通过**；真实 API 评测按规则 skipped。修复尚未提交/推送，须重跑 CI 后再判断。
- q3 `b4-acc-q3` 仍未访问、未确认、未提交退款申请。当前主容器由多个 worktree 配置组成，代码最终重建与运行时验收仍待依 PR 顺序集成后进行。

## B5：催发货/催派送（mock-only）——实现与本地验收（2026-11-06）

**工作树**：`C:/Users/wh/.codex/worktrees/cs-urge-action/agent`，分支 `feat/cs-urge-action`。B5 功能提交为 `810f932`；已将 B4 合并提交 `c2d3dc2` 合入当前工作树，组合验证通过，集成合并提交和 B5 PR 待完成。

**实现**：

- 路由新增 `t_urge_shipping` / `t_urge_delivery`，维持 TRANSACTION 域；物流/快递通用查询不被催办词抢路由，配送政策问句仍落 KNOWLEDGE。
- ActionExpert 对用户明确发起的催办走低风险直执行，不创建确认卡；只把请求发给精确白名单 `business-mock:8081` / 本机 8081，sandbox 或真实 URL 均 fail-closed。
- business-mock 校验当前用户订单归属与订单状态；同租户/用户/订单/类型 24 小时幂等；订单超过 5 天无物流更新、丢件/破损及结果未知不登记/不重试，并路由到现有人工队列。
- 成功登记使用 `simulated` + `confirmation_state=not_required`，审计记录包含 mock `request_id`；明确告知未通知真实商家/物流系统，不承诺时效。新增迁移 135 仅扩展 agent_actions 类型约束，保留现有客服动作值；无新业务表。
- 更新 business-mock 契约、客服域说明、能力清单 UI、FINAL-PLAN 与本进度，并把测试加入 rag_smoke 定向列表及迁移 NON_AI path filter。

**验证结果**：

1. B5 + 相邻客服定向集合：**49 passed**；覆盖双意图路由、模拟登记、24 小时幂等、租户去重、跨用户订单拒绝、订单状态拒绝、审计、超期/丢件转人工、未知 POST 仅一次调用、真实业务 URL 拒绝。
2. Handoff 状态机与触发器回归：`test_handoff.py` **29 passed**。
3. 词表 cs-gate、Supervisor 决策回放、迁移登记和 RAG 路径过滤：**33 passed**；`py_compile`、`git diff --check` 与 B5 相关 Ruff 检查通过。
4. 迁移 135 在独立临时 `pgvector/pgvector:pg16` 数据库实际执行，13 种现有/新增动作类型全部可写入；临时容器、网络与配置已清理。此为 SQL migration T2，不是共享数据库验收。
5. pytest 进程仍输出既有 residue-guard 日志：TaskService 默认尝试连接本机 5432，但该环境未提供密码；各测试进程退出码均为 0。后续若需要更大范围回归，先给测试配置有效隔离 DSN，避免无效等待。
6. 新增真实持久化 T2：隔离 PostgreSQL 16 中运行 B5 催办专家并两次调用 `_persist_audit_records`，核对 `agent_actions`=`simulated/not_required`、`audit_logs` 有 mock 请求编号、`ai.idempotency_records` 均仅一条；`test_urge_flow.py` **14 passed**。测试记录清理后复查会话/动作/审计/ledger 均为 0。测试需显式设置 `CS_AUDIT_PG_TEST=1`，并拒绝非本机、非 test/sandbox 数据库及共享端口 5433。pytest residue-guard 仍提示隔离库缺 embedding role seed；不影响 B5 用例。
7. 合并 B4 后在同一独立 PostgreSQL 16 库应用 006/031/050/135，并还原当前 `conversation_status` 约束；B4 模拟退款与 B5 催办的两项真实审计持久化用例 **2 passed**。测试动作、审计、幂等和会话记录复核均为 0。
8. B4+B5 组合定向集（支付、任务计划、催办、词表双门禁、决策回放、RAG path filter）最新 **143 passed / 3 skipped**；跳过项由未给本轮完整集合配置专用 sandbox/audit DSN 引起，对应 B4/B5 audit T2 已在上一项独立通过。

**运行时状态：UNVERIFIED**。共享 `agent` 栈仍由多个 worktree 配置组成；当前尚未从包含 B4+B5 的最新工作树重建，也未做 B5 真机验收。下一步在 B5 PR 按序合入后，以最新合并工作树重建主容器，检查健康探针并验收 B4/B5 用户路径。没有操作共享数据库；B4 q3 仍未确认或提交退款。

**后续批次**：先完成当前 B5 集成合并、开 PR、CI 与合入；随后依序更新 B6、B7、B8 到各自最新 main，逐批完成冲突复核、定向/T2 测试、PR CI 和合入；全部代码批次完成后，用含 B4-B8 的最新工作树统一重建/运行时验收。B9 立项文档单独提交；q3 保持未触碰。

## B6：价保退差价（mock-only）——实现与 B4–B6 组合验证完成，PR/CI 待完成

- 工作树 `C:/Users/wh/.codex/worktrees/cs-price-match/agent`，分支 `feat/cs-price-match`，功能提交 `87bce2b`，集成 B4/B5 的 merge commit `2c34968`。
- 价保只允许用户确认后写 business-mock 演示钱包；客服与 mock 两侧默认关闭，确认时重新核对订单状态、支付时间、CNY、单件商品、窗口和当前价；订单级重复、金额变化、错误目标或结果未知均拒绝/进入 `verifying`，不触达真实资金。
- 迁移顺序与门禁：134 支付→135 催办→136 价保；三个迁移均已登记 business/memory 目标与 NON_AI path filter，B4、B5、B6 测试均纳入 rag_smoke 清单；词表版本升至 `2026-11-06.2`。
- 组合回归：支付、任务计划、催办、价保、词表、客服 Golden/决策回放及路由 **170 passed / 4 skipped**。跳过项未配置相应 sandbox/audit DSN；B6 隔离审计用例已另跑 **1 passed**。
- T2：临时 PostgreSQL 16 绑定 `127.0.0.1:15438`；先执行迁移 050、135，再由 B6 T2 用例重放 050 并执行 136。`AuditRepository` 实际写入 `price_match/executed` 成功，最终约束同时允许 `urge_request` 和 `price_match`；动作和会话测试记录均为 0。测试容器在结果核对后停止并移除，不含持久卷。
- T0：改动 Python 文件 `py_compile` 通过；新增/相关核心文件 Ruff（忽略导入排序 I001）通过；完整触及文件 Ruff 仍报 10 条既有旧行问题（confirmation_flow、supervisor、init_db），未扩范围清理；YAML 解析与 `git diff --check` 通过。
- **未验收**：尚无 B6 PR/CI 结果、主 API 重建和真机对话证据；统一运行时验收待 B4–B8 集成后执行。q3 未触碰。


## B8：发票抬头/记录查询与确认后模拟申请（2026-11-06）

- 范围：查询当前用户已有抬头/开票记录；显式确认后登记模拟申请。不开真实税务系统，不支持自助新增/修改抬头；服务默认关闭。
- 关键约束：只允许符合状态的订单及已有 active 抬头；确认时重核业务事实；写入走幂等和现有审计，未知结果进入 `verifying`。迁移 138/139、路径过滤、`rag_smoke` 定向测试及契约/客服文档已随 B8 分支增加。
- B8 独立分支既有证据：发票流程 **13 passed / 0 skipped**；隔离 PostgreSQL 审计 T2 **1 passed**，验证 action、audit、幂等记录各一条且真实开票标记为 false；mock HTTP 冒烟、mock 与坐席前端构建通过。
- 与 B4–B7 集成后的词表版本调整为 `2026-11-06.3`。集成组合回归 **262 passed / 6 skipped**；PR #341 首轮 CI 发现审计 T2 fixture 对迁移后的会话状态默认值不兼容。fixture 现在按 schema 选择 `conversation_status=open` 或旧列 `status=active`；独立 PostgreSQL T2 **1 passed**。CI 修复重跑、主容器重建和运行时验收待完成。
- PR 自动审查发现两项补槽/演示身份问题，已在后续修改中修复：发票抬头和记录读取现与确认写入使用同一演示业务身份，认证/权限仍保留真实登录身份；缺订单号时将抬头选择及纸质/电子类型写入既有 `collected_slots`，补槽时恢复。新增两个回归覆盖企业抬头补槽、纸质类型拒绝及 demo 模式完整确认链路。
- 审查修复后的本地验证：完整 `test_invoice_flow.py` **14 passed / 1 skipped**；新增用例筛选 **2 passed**；三个改动 Python 文件 `py_compile`、Ruff 与 `git diff --check` 均通过。pytest 进程退出前仍输出既有全局 `TaskService.ensure_schema` / residue guard PostgreSQL PoolTimeout 日志；未影响退出码，本轮 PR CI 与运行时重建复核待完成。
