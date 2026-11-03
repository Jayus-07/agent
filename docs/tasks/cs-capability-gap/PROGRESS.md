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

合并后真机验收（待执行，对齐 §九.1）：

- A4 容器内重跑 seed：ok=17 failed=0
- A5 rag-service :8090 `POST /retrieve`（kb_id=cs_policy）问「退款政策」命中新文档
- A6 用户端对话（:9080 网关，demo_fuzhou/872）问「退款政策/价保/优惠券能叠加吗/积分怎么用/支付失败怎么办/怎么开票」→ 回复含政策要点 + evidence 引用
- A7 坐席端知识检索（FAQ 层）命中速查问答；主链路零回归（/health + 三端冒烟）

## 后续批次

B2 积分样板（§五模板 A 10 文件）→ B3/B4 并行。B2 定型前不动。
