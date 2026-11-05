# cs-capability-gap 会话交接报告（B4 当前状态，截至 2026-11-06）

> 上游文档（仍然全部有效，本文件不重复其细节）：[FINAL-PLAN.md](./FINAL-PLAN.md)（九批总计划、§四缺口清单、§五文件级方案、§八测试策略、§九验收矩阵——**执行依据**）+ [PROGRESS.md](./PROGRESS.md)（B0-B3 全部验收证据与根因记录；B4 当前实施状态见文末）。
> 纪律基线不变：优先深入功能；满足功能前提下最小改动；意图级接入不平台 Tool 化；每批独立 PR；验收先行（§九矩阵逐条真机验证后才可标完成）；**未实际执行的验证不得报告为通过**。

## 一、总任务

把坐席端「能力差距」表的 8 项未覆盖诉求分九批补齐（B0 顺手 bug → B1 知识底座 → B2-B4 查询型 → B5-B8 动作型 → B9 多模态仅立项），全部按生产标准（迁移/开关/审计/幂等/测试/文档）走独立 PR。RAG 知识面与坐席检索已在 B1/A7 关闭。**查询型接入模板已由 B2/B3 双实例定型，B4 直接复制。**

## 二、进度快照（截至 2026-11-06）

| 批 | 状态 | PR → main |
|---|---|---|
| B0 sandbox 取消 NameError | ✅ | #309 → e2990b9 |
| B1 RAG 知识底座（8 篇政策 + FAQ 治理 14 写入） | ✅ | #312 → 1620a74 |
| A7 坐席知识检索接 RAG 通道 | ✅ | #317 → ce902ea |
| **B2 积分/等级查询（查询型样板）** | ✅ 本会话 | #320 → d1d38df（+docs #321 → da13495） |
| **B3 优惠券查询/用券校验** | ✅ 本会话 | #324 → 55e0fcb |
| B4 支付流水查询 + 重复扣款转退款 | ⏳ 实施中（验收通过，待提交） | q1-q4 真机 PASS；q4 Supervisor 防重复调用已修复，未提交 |
| B5 催办 / B6 价保 / B7 改址 / B8 开票 | ⬜ | B4 定型后 B5-B7 可并行，B8 随时可并行 |
| B9 多模态 | 仅立项文档，不实施 | 并入最后收尾 PR |

- B3 收官快照 main=`55e0fcb`；当前 B4 工作树 HEAD=`7b0bfc5`，`origin/main`=`c877de1`（比工作树多 1 个旅游 UI 提交，尚未 rebase；保留本地未提交改动）。CI 历史基线 935，B4 新增用例数以 CI 结果为准。
- B4 当前共享栈健康；134 迁移已应用，主后端/前端/网关探针通过；宿主 8088 GPU embedding/rerank 已按运维脚本恢复。q4 首轮暴露 Supervisor 重复调用 KnowledgeExpert 并以第二次拒答覆盖第一次有效答复；2026-11-06 已加防重复收尾并真机复验通过，ClaimVerifier 未改。
- 本会话真机验收：B2 4/4 PASS + B3 4/4 PASS（合并后 main 上双探针复证）。

## 三、未完成（含挂账）

**待实施批次**（细节见 FINAL-PLAN §五，B4 详设见本文件 §六）：
1. **B4 支付流水查询 + 重复扣款转退款**——代码已实现，q1-q4 真机验收通过；当前待用户查看 Diff 并确认提交/PR；
2. B5 催发货/催派送（低风险直执行，不承诺具体补发时间——composer ETA 守卫 fail-closed）；
3. B6 价保退差价（**全链路最重**：资金红线三项缺一不合并——终点 business-mock / 开关 fail-closed 双入口 / 审计齐全）；
4. B7 在途包裹改地址（确认流，订单级与账户默认地址严格区分）；
5. B8 开票/抬头（迁移 + 查询 + 动作，抬头子表）；
6. B9 多模态：仅立项记录并入收尾 PR。

**挂账（不阻塞，勿顺手扩大）**：
- ClaimVerifier 误杀有据数字句（B1 q2/q5）：检索完整性调优，等运营反馈再立项，**不硬改护栏**；
- B4 q4 首轮曾因第二次 KnowledgeExpert 调用的 `[E5]` 引用缺失而整答被 ClaimVerifier 拦截；已通过 Supervisor 在同轮成功知识答复后直接收尾修复，ClaimVerifier 保持原样。B1 有据数字句的检索完整性挂账仍单独保留。
- 外部真实系统对接（比价/物流商/支付网关/开票）：mock 契约即边界，只换 adapter；
- 用户端订单页开发票入口：二期；GAP_LIST「注销账号/删除数据」合规行：不在八项范围；
- 杂项：远端分支 `docs/cs-b1-acceptance-final` 清理；隔离容器 `agent-csfix-b0-pg`（15434）待全部批次完工后退役。

## 四、已知信息（本会话实证，接手勿重探；FINAL-PLAN 没有的增量）

### 4.1 查询型模板定型结论（B2/B3 双实例验证，B4 照抄）

模板 A 十文件清单（FINAL-PLAN §五）逐项落地可行，B2/B3 的可复制资产：
- **Service**：`_use_http_gateway()` + `_unwrap` + `_mock_error`（404/409 语义映射）+ `_xxx_view()` 展示归一（kind_label/reason_label/**currency=CNY 必须在 Service 归一层补**——composer 金额守卫按币种比对，facts 缺 currency 会把一切元金额 fail-closed，B3 首轮测试揪出）；
- **facts 三件套（W1 红线，凡查询域必做）**：`_project_<domain>` + projectors 注册 + composer `_fact_amounts` 扫金额键 + `_amount_claim_kind` 加域标签。金额键现状已含：amount/total_amount/balance/recent_amount/points/recent_points/**threshold_amount/discount_value/max_discount/order_total**；标签已含 refund/order/balance/points/**coupon**。B4 的 payment 金额走既有 `order` 标签即可（紧邻订单金额自动归 order 类），如需独立标签再加 `payment`；
- **词表双门禁**：改动必过 `test_cs_eval_gate`（golden 回放）+ `test_cs_decision_replay`（锁版 v2.2，324 条）；政策问句与数据问句同词时，先推演票数再动词表（T-015「会员积分怎么用」、golden 券 3 问句均靠 KNOWLEDGE 2-3 票 > TRANSACTION 1 票保住）；**裸短词（用券/领券）容易漏——B3 真机 q2 实锚「订单 X 用券为什么没生效」fine 词表缺「用券」落域默认答成订单状态**；不确定的问法先加测试再改词表；`VOCAB_VERSION` 升版并同步 test_vocab 断言（现为 **2026-11-04.2**）；
- **测试**：`test_<domain>_flow.py` 复制 `test_coupon_flow.py`/`test_points_flow.py`（http_wire fixture + 路由钉住 + mock 数字一致性 + facts 导出/降级 + 投影白名单/非法枚举 + composer 守卫放行/拒绝 + 隔离 PG sandbox，造数必须重跑幂等）；
- **真机验收脚本**：复用 `d:/tmp/cs_b2_acc.py` / `cs_b3_acc.py` 骨架（登录 demo_fuzhou → mock 直查种子实体号 → POST /api/chat/customer-service 断言要点），session_id 用 `bN-acc-*` 前缀防撞记忆。

### 4.2 B3 新增机制

- mock check 类端点的确定性规则设计：**规则优先级显式化**（expired → threshold_not_met → not_stackable → applicable），每类原因都要有可达样本（券过期靠显式 coupon_code、门槛靠 199 元种子单、一单一券靠核销样本绑定 shipped 种子单）；
- `_coupon_check` 的订单实付在 composer 里按 `order` 类核验（coupon_check 拆两层扫金额：外层 order_total 归 order、内层 coupon 归 coupon）；
- FAQ id=23「一单仅可使用一张优惠券，不可叠加」= mock check not_stackable 文案口径，三方一致（policy §2 / FAQ / mock）。

### 4.3 环境与工具链坑（全部实战踩过）

| 坑 | 正确姿势 |
|---|---|
| **新迁移 CI 门禁**（两次实证） | 迁移文件必须显式登记 `.github/scripts/rag_smoke_path_filter.py`（NON_AI/PAID_RELEVANT 二选一），漏登记 `Detect RAG changes` exit 2、preflight 静默 skipping |
| **marketing/payment 等新 schema** | agent_business 只有 ai/crawler/customer/finance/inventory/order/product/public——新 schema 在迁移文件内 `CREATE SCHEMA IF NOT EXISTS`（133 已示范） |
| **rebuild 轮换窗口验收坑**（B3 合并后终验实锚） | devctl rebuild 尾段服务轮换期间探针会撞到 502/服务暂不可用（app/frontend 重建时差可达分钟级）——**终验前先确认全栈静止**（app/db-migrate 容器 Up > 2 分钟且 :8000 连续 200），再跑探针 |
| devctl 的 MSYS 写法 | `MSYS2_ARG_CONV_EXCL="*" cmd.exe /c "devctl.bat rebuild -y"`（rebuild 不含 frontend-cs） |
| frontend-cs 单独重建 | `docker compose -p agent -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.local.yml build frontend-cs && up -d frontend-cs`（从 worktree 目录跑） |
| Git Bash curl 中文 body | GBK 控制台会搞坏 → python urllib 或 `--data-binary @file` |
| secrets 扫描双闸 | tree 闸：提交前先 `git add` 再跑 `scan_secrets.py --tree`，文档/workflow 绝不写带凭据 URL 原文；history 闸：扫 PR 区间，脏提交可 force-with-lease 重写（未合并分支安全） |
| Mimosa PreToolUse hook | 文件写操作一律走 Write/Edit；commit message 用 `-F file` |
| GitHub 抖动 | push/gh 用 `git -c http.proxy=http://127.0.0.1:7897` / `HTTPS_PROXY=http://127.0.0.1:7897`；连不上时带退避重试（本会话 push 第 3 次才落盘，务必 `ls-remote` 验证） |
| Redis AOF 强杀损坏 | Docker 重启可致 `appendonly.aof.*.incr.aof` 截断 → `docker run --rm -v agent_redis_data:/data redis:7-alpine sh -c "yes | redis-check-aof --fix /data/appendonlydir/<文件>"` 后 start（dev 数据截尾可接受） |
| 测试账号 | 用户端 `demo_fuzhou / Demo@2026`（user_id=872，租户 default；**872 是 user_id 不是密码**）；坐席端 `cs_wire_agent / CsWire@2026`；登录限流：连续失败 4 次触发 429（约 20-30 分钟自解），验密码前先查文档勿试错 |
| 共享栈 .env | `CS_BUSINESS_GATEWAY_MODE=http`（真机走 business-mock）；worktree 有自己的 .env（gitignore，缺失从主仓拷） |
| 并行会话 | rebuild 前必 `git fetch` 查 main 漂移（本会话 B3 开工时撞上 #322 合并 → stash 过桥 rebase）；验收期间日志可能被并行 rebuild 清空，关键探针结论及时落盘 d:/tmp 并回填 PROGRESS |
| worktree 切 main | `git checkout main` 会被主仓占用 → `git checkout --detach origin/main`；未提交改动 rebase 前先 stash |

## 五、工作树与环境现场

- **worktree**：`D:\Program Files\workplace\agent-cs-fix-entry`，分支 `feat/cs-payment-query`，HEAD=`7b0bfc5`；B4 代码与本交接文档均未提交。`origin/main` 有 1 个无关旅游 UI 提交尚未同步；不要 reset/stash 覆盖本地改动。
- 主仓 `D:\Program Files\workplace\agent`；共享栈 compose 项目名 `agent`，端口 8000/8090/8091/9080/5433/3100/3200/3300/8081 由主栈占用。后端通过 `devctl.bat rebuild /y` 从本 B4 worktree 重建，`frontend-cs` 用 compose overlay 单独重建。
- 隔离测试 PG：`agent-csfix-b0-pg` @ 127.0.0.1:15434（postgres/b0sandbox，库 agent_business_sandbox_test；134 迁移测试已执行）；测试 DSN 传 `POINTS_SANDBOX_PG_TEST_DSN`；测试库守卫：库名必须含 test/sandbox。
- 宿主本地模型服务通过本 worktree `dev-local-models.bat start` 恢复，PID 4320，8088 ready；Docker RAG 调 `host.docker.internal:8088`。重启宿主/服务后按运维手册重新检查状态。
- 验收脚本与产物：`d:/tmp/cs_b2_acc.py`、`cs_b3_acc.py`、`cs_b4_acc.py`、`cs-b2-acc-result.txt`、`cs-b3-acc-result.txt`、`cs-b4-acc-result.txt`、`cs-b4-q4-retry.txt`、`cs_b1_a6.py`（B1 骨架）。
- 提交纪律：双重路径限定（`git add -A -- <paths>` + `git commit -F <file> -- <paths>`）；新引用符号的定义文件必须同批提交。

## 六、B4 实施状态与剩余事项（支付流水查询 + 重复扣款转退款）

**当前进度**：实现、T2、阴性验证、worktree rebuild 与 q1-q4 真机通过；q4 已确认根因是 Supervisor 在高置信知识路由下重复派发 KnowledgeExpert，现已在 v1/v2 中增加成功答复收尾守卫，且不改动 ClaimVerifier。B4 仍未提交/建 PR，需用户查看 Diff 并确认后按流程提交。

**意图与路由**：`t_payment_issue`（domain=TRANSACTION，BUSINESS_QUERY，kb_ids=["cs_faq","cs_policy"]）。词表：TRANSACTION 加「支付/扣款/付款失败」域词 + 句式（「订单 X 是不是重复扣款」「支付失败怎么办」注意「支付失败」是政策引导高频句——KNOWLEDGE 现有 k_faq「支付方式」词与 payment-faq-policy 文档在位，**先推演票数再动词表**；payment 金额走 order 标签）；VOCAB 升 2026-11-05.1 + test_vocab 断言同步。

**Service**：`payment_service.py`（双模式，仿 coupon_service）：`get_payments(user_id, tenant_id, order_no)` 返回 `{order_no, total_amount, currency, payments:[{status, amount, paid_at, kind}], success_count}`；分支逻辑在 query 分发层：单笔成功→正常流水答复；失败→引导重试/换支付方式（payment-faq-policy.md 口径）；**同单多笔成功 → 核对流水 → 引导转退款申请**。

**红线（不可妥协）**：重复扣款的退款**必须走既有 `POST /business/refund-requests` 人工审核闸**（CS_REFUND_REQUEST_MODE），B4 只做「核对流水 + 提案/引导」，**绝不新开自动退款通道**；确认卡/提案复用既有 cancel/refund 确认流，不另起炉灶。

**mock**：`GET /business/payments?order_no=`（懒种子覆盖三态：一单单笔成功 / 一单失败可重试 / **一单两笔成功（重复扣款样本）**）；contract.md 升 **v1.4**；只读端点。

**迁移**：`134_cs_payment.sql`——`payment.payment_records`（**payment schema 需迁移内 CREATE**，金额 NUMERIC、status CHECK、order_no 索引、872 幂等种子对齐 mock 三态）+ init_db 登记 "business" + **path_filter NON_AI 登记**。

**facts 三件套**：`_project_payment`（order_no/status/amount/paid_at/currency=CNY/success_count；金额归 order 标签，facts 必须带 currency）；composer 无需新标签（order 已覆盖）。

**测试**：`test_payment_flow.py` 复制 B3 样板（http_wire + mock 三态逐条 + facts + 守卫 + 路由钉住 + 隔离 PG）；**回归测试必须断言「重复扣款答复不含『已退款/已到账』类完成态声明」**（composer 已支付/完成态守卫会兜底，但测试要显式钉住）。

**固定收尾四件（勿漏）**：①GAP_LIST「支付失败 / 重复扣款」行改已覆盖；②`test_payment_flow.py` 进 rag_smoke 定向清单；③迁移登记 path_filter；④customer-service.md 第 8/49 行同步 + payment-faq-policy.md/FAQ/mock 数字三方一致核对。

**执行顺序（验收先行，不可裁剪）**：读 §九.4 → 写验收脚本（复用 cs_b3_acc.py 骨架：q1 单笔成功流水 / q2 失败引导 / q3 重复扣款核对并引导转人工审核）→ 建分支 `feat/cs-payment-query`（从最新 origin/main，先 fetch 查漂移）→ 实现 → T2 全绿 → 阴性验证 → rebuild（终验前确认全栈静止）→ 真机验收 → 回填 PROGRESS → 展示 Diff 请确认 → 提交/PR → CI 对账（`935 + N`）→ 合并 → 主仓 pull+rebuild → 终验 → 停靠收官 + HANDOFF 刷新。

## 七、测试纪律

- B4 原有支付/TaskPlan/CSRouter/FineRouter/composer/vocab 定向集合显式连隔离 PG 15434：**141 passed，零 skip**；本次新增 `test_payment_flow.py` Supervisor v1/v2 回归后，支付流程 + 两套 Supervisor 定向集合 **81 passed，零 skip**。q4 新 session 复验通过，证据见 PROGRESS。
- T2 起步：每批 `backend/tests/customer_service/test_<domain>_flow.py`，断言四件：意图路由命中（含政策问句不翻域钉住）、Service 数据正确（含幂等重放）、facts 导出 + 投影白名单 + composer 守卫放行/拒绝、错误降级话术；
- 词表变更：双门禁 + 全域回归（customer_service 全目录约 9 分钟）；**阴性验证**：撤投影器/撤路由词各跑一次，测试必须失败（证明钉住行为）；
- 本地跑法：worktree 里 `../agent/.venv/Scripts/python.exe -m pytest backend/tests/... -q --no-cov`；隔离 PG 凭据通过 `POINTS_SANDBOX_PG_TEST_DSN` 环境变量传入（指向 15434 隔离库），文档与 workflow 不保存连接凭据；
- CI 对账：preflight `passed = 935 + 新增`，skipped 恒 2；docs-only PR 的 preflight skipping 属预期。

## 八、验收纪律（每批强制）

1. **验收先行**：开工前写好验收脚本与判定标准（脚本骨架见 §4.1）；§九.4 判定 = 问「订单 X 支付失败」→ 失败引导；「是不是重复扣款」→ 核对流水并转人工审核态申请，绝无自动退款话术。
2. **真机前检查**：全栈静止（§4.3 rebuild 轮换坑）+ KB 状态（检索返回 travel/0 命中先查 `auth.knowledge_bases.status`）+ FAQ/mock/迁移数字三方一致。
3. **证据落盘**：真机结果写 PROGRESS.md（含失败与拦截如实记录）；脚本与结果留 d:/tmp。
4. **停止条件**：并行会话冲突先报告不覆盖；任何一步无法安全重建或验证失败，标 BLOCKED/UNVERIFIED 停下报告。
5. **每批共同底线**（§九.10）：/agent、travel、RAG 主链路零回归（:8000 /health + :3100 + :3300 + :9080 登录冒烟）+ CI 全绿逐项核对终态。
