# cs-capability-gap PROGRESS

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

## B8：发票抬头/开票记录查询与确认后模拟申请 —— 实现与局部验收（2026-11-06，分支 `feat/cs-invoice`，基于 `987eddb`，未提交）

**范围**：只补已有抬头查询、开票记录查询和用户确认后的模拟申请；不接真实税务系统。当前不支持用户自助新建/修改发票抬头，坐席 GAP_LIST 明确标为部分覆盖。

**实现**：

- 路由：`t_invoice_query` / `t_invoice_request`；「发票怎么开」等政策问句继续走 KNOWLEDGE。VOCAB_VERSION → `2026-11-06.1`，同步 `test_vocab.py`、cs-gate 与决策回放门禁。
- Service：查询只信当前用户/租户；申请只在 `CS_INVOICE_MODE=mock`、业务网关 `http` 且目标为白名单 `business-mock:8081` 时启用，默认关闭。只允许 paid/shipped/completed 订单、已有 active 抬头和电子发票；提案为 medium risk，必须显式确认。
- Mock：按用户/租户隔离个人与企业演示抬头；确认申请后登记内存态模拟记录。相同 operation key 同 payload 幂等重放；同订单/抬头换 key 冲突。响应固定 `status=simulated`、`real_invoice_issued=false`、`tax_system_contacted=false`；服务重启会清空 Mock 记录，客服 `agent_actions` 留审计。
- 状态与审计：确认时重新核对订单状态和抬头；未知写入结果进入既有 `verifying`，拒绝盲目重试。确认失败审计补齐 `conversation_id`、目标订单；新增 `invoice_request` 到 memory 库动作类型约束。
- 数据/CI/文档：迁移 138→business（抬头、模拟记录表及真实开票恒 false 约束），139→memory（动作类型约束）；`init_db.py` 与 `rag_smoke_path_filter.py` 登记；`rag_smoke.yml` 加入新测试；业务契约升 v1.4；域文档与坐席 GAP_LIST 标部分覆盖。

**验证**：

1. T2 双门禁/查询模板回归：`test_cs_eval_gate`、`test_cs_decision_replay`、`test_vocab`、粗细路由、意图契约、B2/B3、facts：**126 passed, 2 skipped**。
2. 最终跨层回归：`test_invoice_flow`、确认状态机、ActionExpert、缺槽链、QueryExpert：**99 passed**，覆盖缺订单追问、确认后登记、订单状态变更拒绝并审计、重复写冲突、未知结果 verifying、facts 脱敏。
3. 隔离 PostgreSQL 16：138/139 各重复应用两次成功；种子保持 2 条；`real_invoice_issued=true` 与未知 action_type 均被约束拒绝；`invoice_request:simulated` 被 status/type 约束接受。只使用本任务临时 PG，不触碰共享库。
4. 当前工作树构建的独立 `business-mock` 容器 healthy；HTTP 冒烟：health ok、抬头查询、模拟登记、相同键 replay、换键重复 409，两个真实开票标记均 false。独立坐席前端镜像 Next build（含类型检查）成功，`/login` 与 `/cs/knowledge` 均 200 且容器 healthy。B8 `app` 镜像从本工作树构建成功。
5. T0：所有改动 Python 文件 `py_compile` 通过；新服务/测试/路由 Ruff 通过；`git diff --check` 通过。完整 touched-file Ruff 仍报告 15 个既有 lint 项，均在本次未改动的旧行；未顺手清理。

**未完成/风险**：

- 主 API 容器未启动验收。当前共享 Compose 项目 `agent` 的配置文件混有多个工作树；按 `devctl.bat rebuild` 会重建/重启共享 backend 服务，故本轮只构建 B8 app 镜像，不把隔离 API 接入共享数据库。`app` 健康与经 :9080 真机对话记 `UNVERIFIED`。
- 两轮 pytest session-finish 的全局残留守卫尝试连工作树默认 `localhost:5432`，因无 `.env` 密码超时；**不影响 pytest 结果**。未将守卫指向共享数据库。
- 无 PR/CI 结果，未提交、未合并。当前分支基于 `987eddb`，主线尚缺 B4-B7 的 134-137 迁移；合并顺序必须先落 B4-B7，再合 B8（否则后合的旧动作类型迁移可能覆盖 `invoice_request` 约束）。

## 后续批次

- B4 支付查询/重复扣款退款提案已实现，但 PR #336 仍需确认状态并按用户授权合并；资金红线仍是 `refund-requests` 人工审核闸。
- B5-B7 按各自任务工作树继续，不在 B8 工作树改写；B7 当前有独立未提交改动，须保留。
- B8 当前已完成功能实现和局部验收；下一步先让 B4-B7 依序合入主线，再将 B8 分支更新到包含迁移 134-137 的主线，复核并提交/发 PR。`feat/cs-invoice` 工作树保持未提交状态，未经用户确认不提交或合并。

### B8 本轮功能专项复跑（2026-11-06）

- 使用 PYTHONPATH 指向当前 worktree，并通过 --confcutdir=backend/tests/customer_service 避开全局 pytest 收尾数据库清理钩子；test_invoice_flow.py **12 passed，0 skipped**。
- 本次仅复跑客服发票流程测试，不代表主 API 已部署；容器内主 API/真机验收仍为 **UNVERIFIED**。

### B8 持久审计 T2（2026-11-06）

- 在已有 test_invoice_flow.py 中新增真实 PostgreSQL 审计持久化用例；仅允许 loopback 的 test/sandbox 数据库，拒绝共享栈 5433。测试文件已在 rag_smoke 免费档定向清单中，无需新增清单项。
- 使用独立 PostgreSQL 16 容器项目 cs-invoice-audit-t2，绑定 127.0.0.1:15436；测试库依次初始化迁移 006、031、050、139。真实调用 cs_graph_node._persist_audit_records 两次后，验证 agent_actions(action_type=invoice_request,status=simulated)、audit_logs(result=success)、ai.idempotency_records 各仅 1 条，且模拟开票/税务真实副作用标记均为 false。
- 完整发票流程测试 **13 passed / 0 skipped**；T2 运行后动作、审计、幂等和会话残留均为 0。py_compile、Ruff（忽略本文件原有 I001 导入排序项）通过。
- 本批仍未重建主 API；主容器对话和真机验收继续为 **UNVERIFIED**。

\n