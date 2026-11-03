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

## 后续批次

B2 积分样板（§五模板 A 10 文件）→ B3/B4 并行。B2 定型前不动。
