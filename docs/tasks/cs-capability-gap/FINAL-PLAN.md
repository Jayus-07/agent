# cs-capability-gap 实施计划（客服能力差距补齐，供新会话执行）

> 生成：2026-11-04 分析会话。上游依据：坐席端知识助手「能力差距」Tab（用户实测视角）+ 本会话双探查报告（知识/RAG 链路、工具接入模式，结论已内化到本文，接手会话勿重探）。
> 纪律基线：**优先深入功能；满足功能的前提下最小改动；非必要不扩大内容**。所有能力走客服域「意图级能力」接入（与 MallService 同构），**不做平台 Tool 化登记**（capabilities.yaml/tool_registry，tool-skill-guide.md G5 域清单协议明示意图级能力无需登记）。

## 一、目标与范围

**目标**：把坐席端能力差距表的 8 项未覆盖诉求分批补齐；同步把 RAG 知识库从「近乎空库」补到生产可用；全部按生产环境标准改（迁移、开关、审计、幂等、测试、文档齐全）。

**范围内 8 项**（差距表原文口径）：
1. 价保退差价（查询比价 + 资金动作）
2. 催发货/催派送（动作 + 登记）
3. 优惠券叠加/为什么没优惠（账户级查询 + 规则校验）
4. 积分/等级权益（纯查询）
5. 支付失败/重复扣款（查询 + 转退款联动）
6. 在途包裹改地址（订单级动作）
7. 开票/修改发票抬头（查询 + 动作 + 抬头子表）
8. 消息多模态图片（链路级——**本计划只立项不实施**，见 §六 B9）

**范围外**：外部真实系统对接（比价数据源、物流商改址 API、支付网关、开票系统）——当前以 business-mock 为事实源立契约，真实接入时只换 adapter 不改对话层；前端 UI 大改（用户端订单页开发票入口等，对话内可全流程的都不动前端）。

## 二、总计划（九批，每批独立 PR，按序执行）

| 批 | 内容 | 量级 | 依赖 |
|---|---|---|---|
| B0 | 顺手修 bug：mall_service sandbox 取消 NameError | 1 小 PR | 无 |
| B1 | RAG 知识底座（零代码）：8 篇生产级政策文档 + cs_faq 速查 + seed 扩展 | 1 PR（docs+脚本） | 无 |
| B2 | 积分/等级查询（**查询型接入样板，先行**） | 1 PR | 无 |
| B3 | 优惠券查询/券校验 | 1 PR | B2 定型后并行可 |
| B4 | 支付流水查询 + 重复扣款转退款 | 1 PR | B1（支付政策文档） |
| B5 | 催发货/催派送（低风险直执行+登记） | 1 PR | B1（配送政策） |
| B6 | 价保退差价（资金 + 确认流 + 开关） | 1 PR | B1（价保政策） |
| B7 | 在途包裹改地址（确认流） | 1 PR | 无（与 B4-B6 并行可） |
| B8 | 开票/抬头（迁移 + 查询 + 动作） | 1 PR | 无（可并行） |
| B9 | 消息多模态（立项记录，不实施） | 立项文档并入最后收尾 PR | — |

顺序理由：B1 零代码直接消灭差距表「仅能答政策条文」类空白；B2 是查询型样板（B3/B4 复制其结构）；动作型（B5-B8）在查询型验证接入模式后进行；多模态体量与风险完全不同，独立立项。

**每批固定收尾动作**（勿漏）：①坐席端能力差距静态表同步（`frontend-cs/src/app/cs/knowledge/page.tsx:26-42` GAP_LIST，能力上线即把该行改为「已覆盖」或删除）；②新测试文件补进 `.github/workflows/rag_smoke.yml` 定向清单（AGENTS.md #8）；③docs/domains/customer-service.md 如有行为口径变化同步。

## 三、工作树与 Git 规范

- **复用 worktree**：`D:\Program Files\workplace\agent-cs-fix-entry`（本分析会话建立，已停靠 origin/main，工作树干净）。每批从最新 origin/main 新建分支：
  - B0→`fix/mall-sandbox-cancel-nameerror`；B1→`docs/cs-rag-kb-seed`；B2→`feat/cs-points-query`；B3→`feat/cs-coupon-query`；B4→`feat/cs-payment-query`；B5→`feat/cs-urge-action`；B6→`feat/cs-price-match`；B7→`feat/cs-address-change`；B8→`feat/cs-invoice`。
- .env 在 worktree（缺时从主仓拷贝）；GitHub 抖动用 `git -c http.proxy=http://127.0.0.1:7897` / gh 用 `HTTPS_PROXY=http://127.0.0.1:7897`（本会话已实证）。
- 每批：分支→实现→T2→worktree 重建受影响服务→真机/REST 验证→PR→CI 逐项核对（docs-only PR 的 preflight skipping 属预期）→合并→主仓 pull+重建→回填 §七进度。
- 提交双重路径限定（`git add -A -- <paths>` + `git commit -m "..." -- <paths>`）。
- ⚠️ 环境坑（本会话实证，接手必读）：①Docker Desktop Windows 文件共享层有 per-file 陈旧缓存——重建后镜像缺新改文件时，`touch` 改动文件再 build；②改词表必过 vocab_gate；③Mimosa 对 Python f-string SQL 会拦写入——dry-run/统计类脚本用纯 SQL 文件（参照 `docs/tasks/cs-public-launch/W4-dryrun.sql`）。

## 四、系统缺口清单（缺什么才能满足功能）

| 缺口 | 现状 | 补齐方式 | 影响批次 |
|---|---|---|---|
| **业务数据面**：券/积分/支付流水/发票/催办记录 | business-mock 无对象、agent_business 库无表 | mock 内存对象+懒种子（`_STATE_LOCK`+`reset_mock_state`+`operation_key` 幂等，仿 wallet :193）；agent_business 新表走迁移 `backend/sql/migrations/` + `scripts/init_db.py` MIGRATION_TARGETS 登记 `"business"`（**漏登记 init_db 直接报错退出**） | B2/B3/B4/B5/B8 |
| **知识面**：cs_policy 空库 | `scripts/index_cs_seed_docs.py` 仅 8 篇，cs_policy 零种子；源文档散在 docs/customer-service/demo-kb/ | B1：`docs/customer-service/kb/` 入 git + seed 脚本扩展 | B1 |
| **配置面**：动作型无独立开关 | 价保/改址/开票/催办均无开关 | 仿 `CS_ORDER_CANCEL_MODE`（config/customer_service.py:254 + cancel_order_service.py `_ensure_cancel_mode` fail-closed 双入口闸） | B5-B8 |
| **组合层**：每新查询域 facts 断链 | composer LLM 转写的唯一事实源=FactSet 白名单（response/facts.py projectors 表），草稿不进模型上下文 | **每新增查询域必须同步加** `_project_<domain>` 投影器 + 注册 + （涉金额）composer `_fact_amounts`/`_amount_claim_kind` 标签——否则重演 W1 余额问答「查询不到」事故 | B2/B3/B4（凡查询） |
| **坐席端知识检索**：只查 FAQ 层 | ~~cs_knowledge.py:162-181 仅 faq store~~ **A7 已实施（2026-11-04 用户裁决改口径）**：search 合并 RAG 通道（cs_faq/cs_policy 双库 top3，subject_type=customer 同对话授权，失败逐库降级），SearchItem 新增 source=faq\|rag 字段（默认 faq 旧前端零影响） | 已完成（PR：feat/cs-knowledge-rag-channel） | ✅ 已实施 |
| **外部依赖**（真实生产态） | 比价数据源/物流商改址 API/支付网关/开票系统均不存在 | mock 契约先行（contract.md 登记路由+错误语义），真实接入只换 adapter | 挂账，不阻塞 |
| **多模态底座** | ChatRequest 纯文本；vision 模型未注册；无图片存储 | 独立立项（B9 记录），不混入本计划 | B9 |

## 五、各批文件级改动方案（前后端）

### 通用模板 A：查询型（B2/B3/B4，以 t_balance_query 为样板）

改动文件全集（8-10 个/项）：

| # | 文件 | 改什么 |
|---|---|---|
| 1 | `backend/customer_service/vocab.py` | `_CS_DOMAIN_RULES`(:179 起) 加域关键词+句式；**`CS_SIGNAL_EXEMPT_PATTERNS`(:73-81) 必须加新词**（否则被出域词表截胡）；`VOCAB_VERSION`(:34) 升版（触发 vocab_gate 评测门） |
| 2 | `backend/customer_service/router/fine_router.py` | `_RULE_INTENT_MAP`(:12 起) 加关键词→意图 |
| 3 | `backend/customer_service/router/intents.py` | `FINE_INTENTS`(:7-40) + `INTENT_PROFILES`(:43 起)：domain/route_path=BUSINESS_QUERY/`kb_ids=["cs_faq","cs_policy"]` |
| 4 | `backend/customer_service/experts/query.py` | `_INTENT_SERVICE_MAP`(:17-25) + `_dispatch_service`(:427 起) 新分支；返回 `_QueryServiceResponse`（文本草稿 + facts dict，结构仿 balance :434-454） |
| 5 | `backend/customer_service/service/<domain>_service.py`（新） | 双模式：`_use_http_gateway()`(读 `CS_BUSINESS_GATEWAY_MODE`) http 走 `business_client.get_json_sync`→`_unwrap`→`_mock_error`；sandbox 直查 agent_business；错误语义映射表 |
| 6 | `backend/customer_service/response/facts.py` | `_project_<domain>` + projectors 注册 + 新枚举集（**缺口清单第 4 行，必做**） |
| 7 | `backend/customer_service/response/composer.py` | 涉金额：`_fact_amounts` 扫描键 + `_amount_claim_kind` 加标签 |
| 8 | `backend/mock/business_service/main.py` | 内存对象+懒种子+路由+`operation_key` 幂等；`contract.md` 登记契约（版本递增） |
| 9 | `backend/sql/migrations/`（新文件）+ `scripts/init_db.py` | business 库表 + MIGRATION_TARGETS 登记 |
| 10 | `backend/tests/customer_service/test_<domain>_flow.py`（新） | 复制 `test_order_cancel_flow.py` 的 `http_wire` fixture（TestClient 挂真实 mock+打桩 http_client+旁路幂等/metrics），断言意图命中/服务数据/facts 导出/守卫放行 |

前端：**零改动**（用户端对话即入口）。

### 通用模板 B：动作型（B5-B8，以 order_cancel 为样板）

模板 A 的 1-3（`route_path=BUSINESS_ACTION, requires_action=True, risk_level`）+：

| # | 文件 | 改什么 |
|---|---|---|
| A | `backend/customer_service/experts/action.py` | `_INTENT_ACTION_MAP`(:22-30)、订单对象加 `_SLOT_ORDER_INTENTS`(:34)、`_build_proposal`(:561-627) 分发分支、`_ACTION_TYPE_LABELS`(:36-43) 标签 |
| B | `service/<domain>_service.py` | `build_<x>_proposal`（资格重查+提案文本）+ `execute_confirmed_<x>`（确认时重查事实→任何变化拒绝→真实副作用幂等执行，仿 cancel_order_service :80-160） |
| C | `backend/customer_service/confirmation_flow.py` | `_execute_confirmed_action`(:163-258) 加 elif 分支（包在既有 `run_idempotent_side_effect` 里）；成功文案/审计按 action_type 分支(:332-395) |
| D | `backend/config/customer_service.py` | `CS_<X>_MODE` 开关（默认放行/`disabled` 双入口 fail-closed，仿 `_ensure_cancel_mode`） |

前端：**零改动**（确认卡/成功卡复用后端权威 confirmable）。

### B0：mall_service sandbox 取消 NameError（顺手修，先行）

- 现象：`backend/customer_service/service/mall_service.py:546` 在 `_cancel_order_local`（签名 :534-536 未接收 `expected_status`）内引用该变量 → sandbox 模式取消订单 NameError（http 模式不受影响，故冒烟未暴露）。
- 修法：读上下文补齐参数或删死引用（以实际代码为准，最小改）；补 sandbox 路径 T2。
- 测试：sandbox 模式取消订单全链路一条（现有测试全走 http_wire）。

### B1：RAG 知识底座（零代码 + seed 脚本）

- 文档源目录 `docs/customer-service/kb/`（新，入 git 可评审），按库分子目录；扩展 `scripts/index_cs_seed_docs.py`（现 :29-38 种 8 篇，路径约定 `{DOCS_DIRECTORY}/{kb_id}/customer/`）把新文档纳入种子。
- 上传兜底：管理端 `POST /api/rag/upload`（走 pending 审核流，`rag/client.py` 审核不可用 fail-closed 拒答——注意审核服务必须在位）。
- **文档内容要求见 §六**（生产级口径）。
- 验证：重跑 seed → rag-service 检索命中（`/retrieve`）→ 用户端对话问「退款政策/价保/优惠券叠加」回复含政策要点+evidence 引用 → 坐席端知识检索（FAQ 层）命中速查问答。

### B2：积分/等级查询（样板，详设）

- 意图：`t_points_query`（domain=TRANSACTION，BUSINESS_QUERY，kb_ids=["cs_faq","cs_policy"]）。
- Service：`points_service.py`——`get_points(user_id, tenant_id)` 返回 `{points, tier, tier_benefits[], ledger[{direction,amount,kind,memo,created_at}]}`；mock `GET /business/points?user_id=`（懒种子：积分余额 8000 + 等级 gold + 2 笔流水）；迁移 `finance.points_ledger` + `customer.tier_profiles`（最小列，生产口径）。
- facts（**含流水，W1 教训**）：`service_facts={"points": {"points": N, "tier": "...", "recent_amount": ..., "recent_memo": ...}}` → facts.py 加 `_project_points`（points/tier/recent_*，金额进 composer 守卫）。
- 草稿格式照抄 balance 分支（余额+N 等级+最近一笔流水+权益提示）。
- 测试：http_wire 全链路 + facts 投影白名单测试（test_facts.py 风格）+ composer 守卫接受/拒绝。

### B3：优惠券查询/券校验

- 意图：`t_coupon_query`。Service：`coupon_service.py`——`get_coupons(user)`（可用券列表：类型/门槛/面额/有效期/`stackable` 字段/`exclusions`）；`check_coupon(user, order_no)` 返回 `{applicable, reason_code, reason_text}`（mock 刻意种「不可叠加」「不满足门槛」「已过期」三类，让答复有真实规则可讲）。
- facts：`{"coupons": [...], "coupon_check": {...}}` → `_project_coupon`（name/discount/condition/stackable/reason_code；reason_text 走 _safe_text）。
- mock `GET /business/coupons`、`POST /business/coupons/check`；迁移 `marketing.coupons` + `marketing.coupon_redemptions`。
- 政策面答不进的（如「活动价怎么算」）→ kb_ids 落 cs_policy 优惠券规则文档（B1）。

### B4：支付流水查询 + 重复扣款转退款

- 意图：`t_payment_issue`（domain=TRANSACTION）。Service：`payment_service.py`——`get_payments(order_no)`（流水状态/金额/次数）；分支逻辑：单笔成功→正常；失败→引导重试/换支付方式（政策文档）；**同单多笔成功（重复扣款）→ 核对流水 → 生成退款申请（复用既有 `POST /business/refund-requests` 契约 + 人工审核闸），绝不自动退**。
- facts：`{"payments": [{status, amount, paid_at}]}` → `_project_payment`。
- mock `GET /business/payments?order_no=`（懒种子含一单两笔成功样本）；迁移 `payment.payment_records`。
- 红线：重复扣款退款必须走既有 refund-requests 人工审核链路（与 SMOKE-MATRIX S3 同闸），开关沿用 `CS_REFUND_REQUEST_MODE`，不新开自动退款通道。

### B5：催发货/催派送（低风险直执行，不进确认卡）

- 意图：`t_urge_shipping`/`t_urge_delivery`（domain=LOGISTICS，BUSINESS_ACTION，risk_level=low，**requires_action 但走直执行**：action expert 对 low risk 不建确认卡，直接 `_simulate_execute` 模式或真实登记——按 action.py 既有 low-risk 路径接线，探查报告 :1058-1108 的 simulate 分发即现成挂点）。
- Service：`urge_service.py`——`register_urge(order_no, type)` → mock `POST /business/urge-requests`（幂等：同单同类型 24h 内重复催办返回 already_registered，答复「已在处理中」）；答复「已登记催办 + 当前物流状态 + 承诺时效」。
- 迁移：`customer_service.urge_records`（或 business 库 `logistics.urge_requests`，二选一以 action 归属定）。
- 红线：**不承诺具体补发时间**（composer ETA 守卫会拒——`_has_delivery_or_arrival_promise` 对无 facts 的时间承诺 fail-closed），话术只引用政策文档时效。

### B6：价保退差价（资金动作，全链路最重）

- 意图：`t_price_match`（AFTER_SALES，BUSINESS_ACTION，high，确认流）。
- Service：`price_match_service.py`——`build_proposal`：查订单（paid/completed 且在价保窗口）+ 查当前售价（mock `GET /business/products/{no}/current-price`，**业务口径=自营当前售出价**，外部比价挂账）→ 差价=订单价-现价（>0 才可保）→ 提案（差价金额+退回钱包+政策引用）；`execute_confirmed`：重查价格/状态（变化即拒）→ 差价退款走**部分退款契约**（mock `POST /business/refund-requests` 扩展 `kind=price_match` 或新增 `POST /business/orders/{no}/partial-refund`，写 wallet 流水 kind=price_match）→ 开关 `CS_PRICE_MATCH_MODE`（fail-closed 双入口）。
- facts：operation（status/action=price_match/simulated）+ refund 金额——composer 守卫的 refund 语义全部自动生效（金额/到账声明核验）。
- 迁移：`finance.wallet_ledger.kind` 枚举扩展 + 订单价快照列（如需）。
- 红线：资金动作终点 business-mock；不开放真实资金；审计走既有 agent_actions/audit_logs。

### B7：在途包裹改地址

- 意图：`t_change_address_in_transit`（项目当前无 `LOGISTICS` 域，使用 `TRANSACTION` + `BUSINESS_ACTION`；medium，确认流）。
- Service：`address_change_service.py`——`build_proposal`：核实订单和物流状态均为 shipped（在途）→ 提案展示新地址及 1-3 天时效影响、每单限 1 次；`execute_confirmed`：再核状态，变化或已派送/签收即拒绝 → mock `POST /business/logistics/{no}/change-address`，按 confirmation UUID 幂等。
- 与既有 `account_action_service`（账户默认地址）明确区分：本项是**订单级**；缺新地址时走既有 need_info 槽位补充，之后仍需用户确认。真实物流商不接入。
- 迁移：`137_cs_address_change_action_type.sql` 扩展 `agent_actions.action_type` 并保留 B5/B6 动作类型；该迁移须在 135/136 后执行，防止较早迁移收窄约束。

### B8：开票/修改发票抬头

- 意图：`t_invoice_query`（查抬头/开票记录，BUSINESS_QUERY）+ `t_invoice_request`（申请开票，BUSINESS_ACTION，medium，确认流）。
- Service：`invoice_service.py`——`get_titles(user)` / `get_invoices(user)`；`build_invoice_proposal(order_no, title_id, type)` → `execute_confirmed`：mock `POST /business/invoices`（幂等：同单同抬头已开票返回 conflict）。
- 迁移：`customer.invoice_titles`（抬头子表）+ `finance.invoices`（开票记录）。
- 前端：零改动（对话全流程）；用户端订单页入口属二期不扩大。

### B9：多模态（仅立项记录，不实施）

- 缺口：ChatRequest 纯文本（app/api/schemas.py:10）；messages.content_type 字段已有但链路不通；vision 模型未注册（model registry）；无图片存储；PII/合规未评估。
- 立项要点：图片上传 API + 存储 → 消息引用（URL）→ input_guard 多模态扩展 → vision 模型接入（按 registry）→ 前端两端输入/渲染 → 合规（体积限制/脱敏/成本预算）。独立排期，预估 5-8 天。

## 六、B1 生产级文档内容要求（每个文档必须包含的章节）

统一要求：每篇头部含 `版本/生效日期/适用范围`；所有数字型规则用【业务确认】标注（mock 环境无真实业务方拍板，文档先按 mock 行为写、生产前由业务核对）；结尾含「例外与升级路径（转人工）」。文档与 mock 行为必须一致（文档说 24h 审核，mock 就是 24h 内可审——两侧互为事实源校验）。

| 文档（docs/customer-service/kb/cs_policy/） | 必含章节 |
|---|---|
| refund-policy.md 退款退货政策 | 适用条件（7 天无理由/质量问题）；申请入口与流程；**审核时效（人工 24h 内，对齐 SMOKE-MATRIX S3）**；退款路径（**退回账户余额**，对齐钱包实现）；已发货订单走退货退款的差异；不退情形 |
| price-match-policy.md 价保政策 | 价保窗口（下单后 N 天内降价【业务确认】）；差价形式（**退回钱包**）；口径（自营当前售价、券后价/活动价是否参与【业务确认】）；例外商品；申请方式与时效 |
| coupon-policy.md 优惠券规则 | 券类型（满减/折扣/无门槛）；**叠加规则（默认不可叠加）**；门槛计算口径；适用/排除品类；有效期与过期处理；「为什么没优惠」排查路径（未满足门槛/不可叠加/已过期/排除商品四类原因） |
| points-policy.md 积分与会员 | 获取规则（消费 1 元=N 分【业务确认】）；消耗与抵扣；等级体系与权益；有效期与清零规则 |
| payment-faq-policy.md 支付政策 | 支付失败排查（重试/换支付方式/银行卡限额）；**重复扣款处理（核对流水→原路退回，人工介入）**；退款到账时效 |
| shipping-policy.md 配送与运费 | 发货时效（付款后 48h【业务确认】）；配送时效分段；运费规则；超时处理（催办路径 + 例外升级）；改址规则（在途可改/影响时效） |
| invoice-policy.md 发票政策 | 发票类型（电子普票/专票）；开票时限；抬头修改规则（未开票可改/已开票红冲重开【业务确认】）；申请路径 |
| aftersales-details.md 售后细则 | 破损/错发处理；仅退款 vs 退货退款适用；运费承担；时效 |

cs_faq 库：新增 1 篇「高频问题速查」（20-40 条 Q&A，与上表一一对应的短问答）——进 FAQ 精准层供坐席速查；用户侧高频问答继续经坐席端「FAQ 草稿→审核」流沉淀（真实数据驱动）。

## 七、进度

| 批 | 状态 |
|---|---|
| B0 | ✅ 合并部署验收（PR #309 → e2990b9：sandbox 取消 NameError 修复 + 隔离 PG T2 + CI 真跑） |
| B1 | ✅ 合并（PR #312 → 1620a74）+ 真机验收闭环 + A7 落地（PR #317 → ce902ea，2026-11-04 全部完成）：**验收揪出先于 B1 的检索链断裂双根因并已修复**——①6 个 cs KB 曾于 10-22 被批量置 inactive（auth.knowledge_base_audits 留痕，super_admin 279）→ 已按 store API 全部启用（v2）；②向量索引身份 vs 注册表 source 标签 bug 已随并行会话 #311-#314 修复；A5 检索 7/7 命中、A6 对话 4/6 直接达标（q3 券叠加经 FAQ 治理后 3/3），q2/q5 ClaimVerifier 拦截为 fail-closed 正确行为（检索完整性调优 P2 挂账）；FAQ 治理 14 写入对齐新政策；A7 坐席端 RAG 通道真机验收：搜「优惠券叠加」7 条 = FAQ 1 + RAG 6（含 coupon-policy 新文档），source 区分正确、两通道口径一致互证；主链路零回归（/health ok + 17 容器 healthy） |
| B2-B9 | ⬜ 待实施（B2 积分样板为查询型接入首批） |
| 挂账：外部真实系统对接（比价/物流商/支付网关/开票） | ⬜ 不阻塞 mock 契约落地 |
| 挂账：坐席端知识检索接 RAG | ✅ 已实施（A7，2026-11-04 用户裁决：坐席端接 RAG 通道；T2 见 backend/tests/api/test_cs_knowledge_search_rag.py） |
| 挂账：用户端订单页开发票入口 | ⬜ 二期 |

## 八、测试策略

- 每批 T2 起步：`backend/tests/customer_service/test_<domain>_flow.py` 复制 `http_wire` 模式（真实 mock 契约、非共享 fixture）；断言：①意图路由命中（fine_router+vocab）②Service 数据正确（含幂等重放）③**facts 导出 + FactSet 投影白名单 + composer 守卫放行/拒绝**（W1 教训三件套，凡查询域必测）④动作型：确认执行成功/事实变化拒绝/开关 disabled 双入口全拒/未知值 fail-closed。
- 词表变更：升 `VOCAB_VERSION` 过 vocab_gate；不扩 mock、不 skip。
- 每批测试文件补进 rag_smoke 定向清单；CI 逐项核对终态（docs-only 的 preflight skipping 属预期）。
- 每批 worktree 重建受影响服务后真机验证：用户端(:3100) 对话实测目标问法→回复含真实数据/动作闭环；坐席端(:3300) 巡检相应视图。
- 验收后同步：能力差距静态表 + docs/domains/customer-service.md（口径变化时）。

## 九、验收矩阵（全绿才算能力补齐完成）

1. B1：问「退款政策/价保/优惠券能叠加吗/积分怎么用/支付失败怎么办/怎么开票」→ 回复含政策要点 + evidence 引用（不再「仅能答条文」或空答）；坐席端知识检索命中速查问答。
2. B2：问「我的积分/等级权益」→ 回复含积分余额、等级、最近流水；异常礼貌降级。
3. B3：问「我的优惠券/为什么没优惠」→ 回复含真实券列表/不生效原因（四类原因之一）。
4. B4：问「订单 X 支付失败/是不是重复扣款」→ 失败引导；重复扣款核对流水并转退款申请（人工审核态）。
5. B5：问「催发货」→ 物流状态 + 已登记催办；同单重复催办幂等答复；不承诺具体时间。
6. B6：问「买完降价了退差价」→ 价保资格判定 → 差价提案卡 → 确认 → 钱包入账（kind=price_match）→ 坐席端可见；disabled 全拒。
7. B7：在途订单改址 → 提案（新地址+时效告知）→ 确认 → mock 记录变更；已签收拒绝。
8. B8：查抬头/申请开票 → 抬头列表/提案 → 确认 → 开票记录；重复开票幂等。
9. B0：sandbox 模式取消订单不再 NameError。
10. 全程：/agent、travel、RAG 主链路零回归（:8000 /health + :3100 + :3300 + :9080 登录冒烟）；每批 CI 全绿。

## 十、风险与停止条件

- 词表改动影响全局路由（出域/澄清/动作域守卫联动）——每批 vocab_gate + 路由回归必跑；不确定的问法先加测试再改词表。
- 资金类（B6）：终点 business-mock、开关 fail-closed、审计齐全三项缺一不合并。
- 外部依赖（真实比价/物流商/支付网关/开票）不在本计划内，mock 契约即边界；接手会话不得擅自引入外部 API 调用。
- 共享栈重建有秒级中断窗口（避开并行会话全量 pytest 窗口）；GitHub 抖动按 §三代理处理；任何一步无法安全重建或验证失败，标 BLOCKED/UNVERIFIED 并停下报告。

## 十一、给接手会话的启动清单

1. 读本文件 + `docs/tasks/cs-public-launch/`（同 worktree 上一任务，环境机制、测试账号、`.env` 状态全部适用）。
2. `git -c http.proxy=http://127.0.0.1:7897 fetch origin` 核对 main ≥ ab22fb2；worktree `agent-cs-fix-entry` 应干净停靠 origin/main。
3. `docker ps` 全量健康（app:8000/frontend:3100/frontend-cs:3300/apisix:9080/business-mock:8081/postgres:5433）；测试账号 demo_fuzhou(872)/cs_wire_agent(supervisor)。
4. 从 **B0（bug 小修）→ B1（知识底座）→ B2（积分样板）** 开始；B2 定型后再并行 B3/B4。每批按 §二固定收尾动作 + §八测试纪律走完整闭环。
5. 探查结论已全部内化本文（file:line 均已核实），**勿重复探查**；遇代码与本文行号漂移以代码为准。
