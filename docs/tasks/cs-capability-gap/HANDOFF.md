# cs-capability-gap 接手实施计划（B2-B9，供下一会话执行）

> 生成：2026-11-04 施工会话（B0/B1/A7 已完成收官）。上游文档：[FINAL-PLAN.md](./FINAL-PLAN.md)（总体九批计划、§四缺口清单、§五文件级方案、§六文档要求、§八测试策略、§九验收矩阵——**仍然全部有效，本文件不重复其内容，只写增量与修正**）+ [PROGRESS.md](./PROGRESS.md)（B0/B1/A7 全部验收证据与根因记录）。
> 纪律基线不变：优先深入功能；满足功能前提下最小改动；意图级接入不平台 Tool 化；每批独立 PR；验收先行（§九矩阵逐条真机验证后才可标完成）。

## 一、当前状态（2026-11-04 收官快照）

| 项 | 状态 |
|---|---|
| main | `61d4226`（PR #318 合并；B0=#309→e2990b9、B1=#312→1620a74、A7=#317→ce902ea 均已合并部署） |
| worktree | `D:/Program Files/workplace/agent-cs-fix-entry`，detached 于 origin/main，干净 |
| 共享栈 | 全量 healthy（app:8000/frontend:3100/frontend-cs:3300/apisix:9080/business-mock:8081/postgres:5433）；rebuild 后容器内已含 kb/ 文档与 A7 代码 |
| CI 基线 | rag_smoke preflight **907 passed / 2 skipped**（@61d4226；2 skip 为既有归因）——新批次 CI 对账公式：`907 + 新增测试数 = passed`，多了少了都要查 |
| B1 验收 | ✅ 全链路关闭：A4 seed ok=17 / A5 检索 7/7 / A6 六问 4/6 直接达标（q2/q5 ClaimVerifier fail-closed 拦截=P2 挂账）/ A7 坐席端双通道真机 7 条互证 / 主链路零回归 |
| 待实施 | B2（积分样板）→ B3/B4 → B5-B8（B3/B4 可与 B2 定型后并行）→ B9 仅立项；每批固定收尾见 FINAL-PLAN §二 |

## 二、启动清单（下一会话 5 步进场）

1. 读本文件 + FINAL-PLAN.md（§五方案与 §九验收仍按它执行）+ PROGRESS.md（验收证据风格与根因记录）。
2. `git -c http.proxy=http://127.0.0.1:7897 fetch origin` 核对 main ≥ `61d4226`；worktree detached 停靠 origin/main；每批从最新 origin/main 开分支（分支名 FINAL-PLAN §三已定）。
3. `docker ps` 全量健康；**注意并行会话频繁 rebuild 共享栈**（B0-A7 期间 #311/#314/#316 都是并行会话合并的）——动容器前先 `git pull` 确认本地 main 不落后，否则会部署旧代码（本会话实际踩过：pull 网络失败后 rebuild 部署了旧 main）。
4. 从 **B2 积分样板**开始（B2 定型后 B3/B4 可并行）。不要重探查：链路结论全部在本文件 §三，FINAL-PLAN §五的 file:line 有漂移处以代码为准。
5. 验收先行：每批开工前先读 §九 对应条目，设计好真机验收脚本再动手（验收脚本模式见 §四.6）。

## 三、本会话实测获得的链路机制（FINAL-PLAN 没有或需修正的）

以下全部为本会话真机/容器内实证，接手会话直接引用，勿重探：

### 3.1 KB 授权链（B1 验收最大根因的机制，B2-B8 都依赖）

- 运行时 KB 注册表 = **DB 表 `auth.knowledge_bases`**（非代码静态表；代码表仅 bootstrap，`config/knowledge_base.py::get_knowledge_bases` 每请求实时读库）。
- customer 主体可见集 = `audience='customer' AND status='active'` 的全部库；**inactive 库对一切检索不可见**（`_keep` 在 `rag/pipeline.py::retrieve_documents` 强制授权过滤）。
- ⚠️ 历史教训：6 个 cs KB 曾于 2026-10-22 被 super_admin(id=279) 批量置 inactive → 全部 cs 检索静默死亡、BM25 全局兜底返回 travel 内容。**每批验收若见「检索返回 travel 内容/0 命中」，第一反应查 `auth.knowledge_bases.status`**（只读 SQL 或 `list_knowledge_bases(include_inactive=True)`）。
- 激活/停用走 `rag/knowledge_base_store.py::activate_knowledge_base`（version CAS + 审计），不要裸 UPDATE。
- B2-B8 不新增 KB（复用 cs_faq/cs_policy），无需动注册表。

### 3.2 检索与对话链路

- 向量身份已对齐：索引 `custom-gpu/bge-m3`（fingerprint 9b0cdc11…）= 注册表解析身份；embedding 服务 host.docker.internal:8088 健康。fresh 进程必须先 `refresh_registry()` 否则报「数据库未配置可用的 embedding 供应商 API Key」（rag-service 启动时自动做）。
- `rag-service :8090 /retrieve_docs`：`{question, kb_id, top_k, subject_type:'customer', tenant_id:'default'}`；返回 `docs[{content, metadata}]`，metadata 白名单含 `doc_id/kb_id/source_file/section_title/score`（score=**向量距离，越小越相关**；BM25 补充结果无 score）。dev 环境 AI_INTERNAL_TOKEN 为空=跳过鉴权（.env 无 AI_INTERNAL_TOKEN/INTERNAL_API_TOKEN 变量）。
- KnowledgeExpert 检索顺序：**先 FAQ 层（ai.cs_faq 表，jaccard≥0.30，60s 索引缓存）命中即答**，不中才走 RAG（kb_ids 按 intents.py 意图声明）。**FAQ 里有错误口径会挡住正确的 RAG 文档**——B2-B8 每批收尾必须检查 ai.cs_faq 是否有与该域新政策/mock 矛盾的条目（本会话治理了 13 条，方法见 §四.5）。
- ClaimVerifier（rag-service /ask 内）：生成回答中的数字/事实句必须在证据 chunk 中找到支撑，否则 fail-closed 拒答（日志 `[RAGChain] 拒答 layer=claim_verify reason=hallucination`）。**已知误杀模式**：政策文档里的数字句（如「30 分钟有效」「15 天窗口」）若未被完整检索进证据，会被拦。B2 查询域的真实数据走 facts 投影（不走 /ask），不受此影响；政策问答里再遇到「生成回答未能通过事实一致性校验」属此机制，是**正确护栏行为**，P2 挂账勿硬改。
- 网关对话链路：`POST :9080/api/chat/customer-service`，需 `X-API-Key` 头（值=主仓 .env `API_KEY`）+ `Authorization: Bearer <login token>`。ChatResponse 的 `sources` 字段在 CS 路径恒空，evidence 以文内引用呈现（`[E1]`/`参考文献`/`--- 参考来源`）。

### 3.3 业务数据面与幂等（B2 直接要用）

- business-mock（`backend/mock/business_service/main.py`）：内存对象 + `_STATE_LOCK` + `reset_mock_state()` + `operation_key` 幂等，钱包实现 `_wallet_for/_wallet_append`（:193 起）——B2 mock 积分对象照此模式。
- mock 契约登记：`backend/mock/business_service/contract.md` 版本递增。
- agent_business 新表：迁移放 `backend/sql/migrations/`（当前最大 131）+ **`scripts/init_db.py::MIGRATION_TARGETS` 登记 `"business"`（漏登记 init_db 直接报错退出）**。
- sandbox 直查链路写池= `infra.db.get_business_engine()`（BUSINESS_DB_CONFIG），只读池= `sql/executor` 模块级单例（BUSINESS_DB_READONLY_CONFIG）——**测试里 monkeypatch 配置后必须调 `executor._close_pool()` + `infra.db.reset_engines()` 逼重建**（参考 test_mall_cancel_sandbox_pg.py）。

### 3.4 坐席端与 FAQ 治理机制

- A7 已上线：`/cs/knowledge/search` = FAQ 层 + RAG 层（cs_faq/cs_policy 双库 top3，source=faq|rag，失败逐库降级）。B2+ 无需再动它。
- FAQ 写入走 `FAQStore.upsert_faq(question, answer, status='published', kb_refs=…)`：**faq_key=normalize_question(question)**，同 key 幂等更新、保留 id/hit_count、立即失效索引。⚠️ 暗行陷阱：历史行的 faq_key 可能与当前 question 不一致（如 id=538 key=「退款到账时长」）——治理时先查 `faq_key` 实际值再按 key 更新。
- B2 联动点：ai.cs_faq 中积分/等级相关条目（id=8 会员等级、id=16 积分累计）已在 B1 治理时按 points-policy.md 草案口径改写（100 分=1 元、白银≥500/黄金≥2000/铂金≥8000、近 90 天评定）——**B2 的 mock 实现必须与这些数字一致**（文档与 mock 互为事实源；数字全部带【业务确认】，生产前业务复核）。

### 3.5 环境与工具链坑（本会话全部实战踩过）

| 坑 | 正确姿势 |
|---|---|
| Git Bash 调 devctl | `MSYS2_ARG_CONV_EXCL="*" cmd.exe /c "devctl.bat rebuild -y"`（`/y` 会被 MSYS 转换吞掉；rebuild 覆盖 backend+容器化前端） |
| Git Bash curl 中文 body | 会被 GBK 控制台搞坏 → 用 `python -c` + urllib 或 UTF-8 文件体 `--data-binary @file` |
| secrets 扫描 tree 闸 | workflow/文档**绝不写带凭据 URL 原文**（`postgresql://user:pass@…` 形态即拦，事故记录也只写形态描述）；测试库连接用「库名 env + job 既有 PG* 变量」模式（见 test_mall_cancel_sandbox_pg.py 的 DSN/DBNAME 双路径） |
| secrets 扫描 history 闸 | 扫 PR 提交区间——脏提交进分支后必须 force-with-lease 重写历史（未合并分支可安全做）；**本地 scan 只扫已跟踪文件，提交前先 git add 再跑** |
| Mimosa PreToolUse hook | Bash 命令文本里出现 .py 源码路径写操作/git commit heredoc 内嵌代码会被拦 → 文件一律走 Write/Edit；commit message 用 `-F file` 或 `-F -`（stdin） |
| worktree 切 main | `git checkout main` 会报被主仓占用 → 用 `git checkout --detach origin/main` |
| 测试账号 | 用户端 `demo_fuzhou / Demo@2026`（user_id=872，租户 default；**872 是 user_id 不是密码**）；坐席端 `cs_wire_agent / CsWire@2026`（auth id=24，supervisor） |
| 登录限流 | 连续失败 4 次触发 429 临时锁定（约 20-30 分钟自解）——验密码前先查文档勿试错 |
| 隔离测试 PG | 容器 `agent-csfix-b0-pg` @ 127.0.0.1:**15434**（postgres/b0sandbox，库 agent_business_sandbox_test，已应用 003+125 迁移）——B2 若需 business 库表集成测试可复用（建新迁移表即可）；测试库守卫：库名必须含 test/sandbox |
| 主仓/并行会话 | 共享栈 rebuild 前必 `git pull`（fetch 失败会部署旧 main）；验收期间日志可能被并行 rebuild 清空——关键探针结论及时落盘 d:/tmp 并回填 PROGRESS |

## 四、B2 积分/等级查询（首批，详设补充）

FINAL-PLAN §五 B2 + 模板 A（10 文件清单）仍为准，以下为本会话增量信息：

1. **意图与词表**：`t_points_query`（domain=TRANSACTION，BUSINESS_QUERY，kb_ids=["cs_faq","cs_policy"]）。词表改动必过双门禁：`test_cs_eval_gate`（三份 golden 回放）+ `test_cs_decision_replay`（锁版 v2.2）——不确定的问法先加测试再改词表；`CS_SIGNAL_EXEMPT_PATTERNS` 必加新词（否则被出域词表截胡）；`VOCAB_VERSION` 升版。
2. **Service 双模式**：`points_service.py` 仿 balance——`_use_http_gateway()`（CS_BUSINESS_GATEWAY_MODE）http 走 `business_client.get_json_sync` → `_unwrap` → `_mock_error`；sandbox 直查 agent_business 新表。mock `GET /business/points?user_id=`（懒种子：积分 8000 + 等级 gold + 2 笔流水——数字必须与 points-policy.md 和 FAQ id=8/16 一致）。
3. **facts 三件套（W1 红线，必做）**：`response/facts.py` 加 `_project_points` + projectors 注册；涉金额/流水数字进 `composer._fact_amounts`/`_amount_claim_kind` 标签——否则重演「余额问答查询不到」事故。测试必须覆盖 facts 导出 + 投影白名单 + composer 守卫放行/拒绝（test_facts.py 风格）。
4. **迁移**：`finance.points_ledger` + `customer.tier_profiles`（最小列、snake_case、id/created_at/updated_at 必备、金额 DECIMAL）+ init_db.py MIGRATION_TARGETS 登记。
5. **测试**：`backend/tests/customer_service/test_points_flow.py` 复制 test_order_cancel_flow.py 的 `http_wire` fixture（TestClient 挂真实 mock + 打桩 http_client + 旁路幂等/metrics）；PG 级集成可复用 15434 隔离容器（建新迁移表，注意只读池 close_pool 重建）。
6. **真机验收脚本模式**（§九.2：问「我的积分/等级权益」→ 回复含积分余额、等级、最近流水；异常礼貌降级）：复用 `d:/tmp/cs_b1_a6.py` 骨架——登录 demo_fuzhou → POST /api/chat/customer-service（X-API-Key + Bearer）→ 断言回答含要点 + evidence。会话 ID 用新前缀（如 b2-acc-q1）避免撞记忆。参考文件在本会话结束前保留在 d:/tmp。
7. **固定收尾**（勿漏）：①GAP_LIST（frontend-cs `knowledge/page.tsx` :26-42）「积分怎么用 / 等级权益」行——上线后 behavior 改「已覆盖（意图级查询）」或按既有风格标注；②新测试文件进 rag_smoke 定向清单；③customer-service.md 行为口径如有变化同步；④FAQ/政策文档与 mock 数字一致性核对（§3.4）。

## 五、B3-B9 速览（增量提示）

| 批 | FINAL-PLAN 方案 | 增量提示 |
|---|---|---|
| B3 优惠券查询 | §五 B3 | mock 刻意种「不可叠加/不满足门槛/已过期」三类样本；FAQ id=23 已按新口径治理——mock 行为与其一致 |
| B4 支付查询+重复扣款 | §五 B4 | 红线：转退款必须走既有 refund-requests 人工审核闸（CS_REFUND_REQUEST_MODE），绝不自动退；payment-faq-policy.md 已就位 |
| B5 催办 | §五 B5 | low-risk 直执行路径在 action expert 既有 simulate 分发挂点；**不承诺具体补发时间**（composer ETA 守卫 fail-closed） |
| B6 价保 | §五 B6 | 资金红线三项缺一不合并：终点 business-mock / 开关 fail-closed 双入口（仿 `_ensure_cancel_mode`）/ 审计齐全；价保窗口 15 天等数字已入 price-match-policy.md（【业务确认】） |
| B7 改址 | §五 B7 | 订单级改址与 account_action_service（账户默认地址）明确区分；shipping-policy.md 已写「在途可改/每单 1 次/影响时效 1-3 天」——mock 与文档一致 |
| B8 开票 | §五 B8 | 抬头子表 + finance.invoices；invoice-policy.md 已就位（普票 1 工作日/专票 3 工作日/90 天换开）；FAQ id=15/27/534 已对齐 |
| B9 多模态 | §五 B9 | 仅立项文档，不实施，不混入任何批次 |

**每批共同验收底线**（§九.10）：/agent、travel、RAG 主链路零回归（:8000 /health + :3100 + :3300 + :9080 登录冒烟）+ CI 全绿逐项核对终态。

## 六、P2 挂账与已知残留

1. **ClaimVerifier 误杀有据数字句**（B1 验收 q2 价保/q5 支付）：检索完整性（rerank/top_k）与校验容差调优——B2+ 查询域真实数据走 facts 不受影响；政策纯问答场景若运营反馈再立项，不硬改护栏。
2. 远端分支 `docs/cs-b1-acceptance-final` 删除失败（GitHub 代理抖动）——已合并无害，顺手时清理。
3. 隔离容器 `agent-csfix-b0-pg`（15434）常驻——全部批次完工后可 `docker rm -f` 清理。
4. GAP_LIST 各行 missing 均为 Tool/数据面（B2-B8 范畴），B1/A7 不改行——每批收尾按 §四.7 同步对应行。
5. 10-22 批量停用 cs KB 的背景无人可考（审计无 reason 字段）——PROGRESS 已留痕；KB 状态现在全部 active，勿重复处置。

## 七、验收纪律（每批强制，不可裁剪）

1. **验收先行**：开工前写好 §九 对应条目的验收脚本与判定标准；实现完先 T2（http_wire + facts 三件套 + vocab_gate）→ 本地全绿 → 阴性验证（撤改动测试必须失败，证明测试钉住行为）→ PR → CI 精确对账（passed 数 = 基线 + 新增数）→ 合并 → 主仓 pull+rebuild → 真机验收逐条过 → 回填 PROGRESS/§七。
2. **验证证据落盘**：真机验收结果写 PROGRESS.md（含失败与拦截，如实记录；「未实际执行的验证不得报告为通过」）。
3. **提交纪律**：双重路径限定（`git add -A -- <paths>` + `git commit -F <file> -- <paths>`）；提交前 git add 后跑本地 scan_secrets；新引用符号的定义文件必须同批提交。
4. **停止条件**：真机验收发现「检索返回 travel/0 命中」先查 KB 注册表状态（§3.1）；遇并行会话冲突（共享栈 rebuild 撞车、工作区被收编）先报告不覆盖；任何一步无法安全重建或验证失败，标 BLOCKED/UNVERIFIED 停下报告。
