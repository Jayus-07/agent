# PR-INTEGRATION-20261016

## 目标与执行方式

以 GitHub 当前状态为准，完成 PR 缺陷修复、合并遗留问题处理、可解决阻断排除、Review 收口、按门禁合并及 main 集成验收。此文件与 PROGRESS.md 是本任务唯一进度记录；用户 2026-10-16 提供的完整任务要求为权威细则。

施工循环：核实事实 → 定位根因 → 修改代码和回归测试 → 按风险做实际验证 → 提交并更新 PR → 处理 Review → 检查当前 HEAD CI → 达门禁后正常合并 → main 定向验收。不得把报告、静态检查或 skipped job 当成施工完成/真实验收。

## 授权和安全边界

本任务已授权：检查 Git/GitHub/Review/CI；建立或复用隔离 Worktree；修改任务范围代码、测试、必要配置/迁移/文档；运行隔离服务和定向测试；推送本任务 PR；创建 Follow-up PR；真实修复后回复并解决 Review；满足门禁后正常合并；合并后定向集成回归。

未授权：强推/删除他人提交；修改共享或生产数据库；泄露或复制生产密钥；绕过分支保护或 Required Checks；未经授权发起付费 Provider/大规模评测；用假价格/假响应/假数据宣称真实验收；真实 P0 风险仍合并；强制删除含修改或需保留文件的 Worktree。

阻断必须记录原始错误、命令、环境、HEAD、影响范围、根因分类、已试路径和下一项可继续工作。能在隔离环境安全修复的继续施工；外部阻断只阻塞对应 PR。

## 执行原则

- 不修改有用户改动的 main 工作树；每条施工线使用隔离工作树。
- 不覆盖其他会话的工作树或未提交内容。原 PR 工作树优先复用；无法安全接管时，从 PR HEAD 建新工作树，最终仅快进更新其远端 PR 分支。
- 最多三条并行施工线；GitHub 合并串行执行。
- 不扩大到全仓审计；按现有 Router/Runtime/Domain/Tool、迁移、状态机、授权和账本实现修复。
- 不增加超时掩盖问题，不删测试、不扩大 mock、不 skip/xfail、不降低 Golden 门槛。
- 修改功能必须有对应实际验证。未执行或被 skip 的验证只能标 NOT VERIFIED/BLOCKED。
- 每项变更前查看相关 Git diff；提交后检查 diff 与密钥扫描。
- 按仓库 T0–T3/testing-guide 选最小充分验证，不默认全量回归。

## PR 依赖与施工范围

### 快速修复队列

1. **#73 Provider/OCR 健康探测**：保留已登记模型的上游错误状态，不把上游 model_not_found 误作本地 404；手动探测复用可信操作者服务端限流；模型目标/授权配置变化或软删除重新启用时失效人工健康缓存；覆盖 API、状态、配置、前端刷新。解决 3 条 Review，基于最新 main 解决冲突，核实迁移 097 顺序和注册，当前 HEAD CI 与必要 T0–T3 均过后合并。
2. **#72 已合并遗留**：创建 Follow-up PR。Trip 创建成功后用服务端确认的 trip_id 写 URL，刷新/清除 localStorage/跨浏览器恢复；恢复失败不能悄悄创建重复 Trip，服务端仍做 owner/tenant 授权。只读问答给模型有预算、有结构且经过敏感信息过滤的正式 Trip 投影：目的地/日期、每日地点/地址/备注、酒店/交通/其他 arrangements、版本、Active Trip 和草案状态；备注作为不可信内容；缺少字段时不得编造。覆盖 TR-01～TR-14，回到 #72 原 Review 线程链接 Follow-up 和证据。
3. **#70 已合并遗留**：创建独立 Follow-up PR，只解决同租户/用户/订单跨会话退款申请并发唯一性。先检查正式 schema 与真实数据重复情况；新增顺序迁移及仅 refund + 活跃状态的 PostgreSQL 部分唯一性；不自动改旧数据，NULL/空订单 ID 不可绕过。保留应用预检，数据库作最终并发裁决；冲突后重查权威案件；同确认 ID 同载荷幂等复用，不同确认 ID 明确冲突；写后未知进入核实语义。真实隔离 PostgreSQL 覆盖 CS-01～CS-12；不得把申请登记称为资金退款成功。

### 主助手队列

4. **#69 Conversation-first**：在现有 Product/Tenant/User/Session/Request 可信作用域上修复跨请求事件隔离；不确定归属的晚到后台事件不得投给当前 SSE；验证 AG-01～AG-07。维持 Router → Runtime → Domain/Skill/Tool；验证一般问答、上下文、授权 RAG/SQL、产品边界、拒绝绕权、多意图和受控澄清。排除隔离 Next BFF/API Key/APISIX/PostgreSQL/Redis/Registry/角色绑定/费用链配置问题；真实凭据只能经既有合法机制注入。验证 AG-08～AG-20：真实 LLM、SSE、身份绑定、RAG 引用、只读授权 SQL、取消/恢复、Trace 与模型费用归因及安全失败。真实凭据/模型质量缺失则仅此 PR 保持 Draft/BLOCKED，继续其他工作。

### RAG 队列

5. **#66 RAG 上传**：修复 PDF 页码清洗误删独占短数字行；根据页眉/页脚/页面位置和重复模式识别，不确定则保留正文，保护年份、条款、SKU、库存、金额。检查真实 PDF Worker SIGKILL 全链路证据（task/worker/源码 HEAD、内存事件、信号、并发、解析峰值、表格抽取、chunk、超时和 lease/recovery 状态），再按根因最小修复并复用现有恢复机制。验收 RAG-01～RAG-12，尤其真实 PDF IndexRun→QualityGate→Active→Search；不将 Worker 死亡或 OOM 猜测当根因。
6. **#52 MultiQuery/费用**：在 #66 基线集成后顺序验证统一 LLMProxy、角色/模型、真实 actor/tenant/run/request、单次调用单次计费、缓存、Token/费用精度、权威价格、预算预占/结算、线程/异步身份和取消/重试账本。仅在隔离环境且已获授权价格/预算时做真实评测；当前 HEAD 用原指标和 Top-1 0.80 门槛复验。不得伪造价格或发起未授权付费请求。验收 RAGC-01～RAGC-10。

### 过期 Draft

7. **#50 FE-4**：比对三项前端改动与 #72/后续 main；确认按钮门禁和独立价值。全覆盖则记录替代提交/测试并关闭 Draft；仍有缺口则提取小型 Follow-up 后再关闭旧 Draft。
8. **#60 客服交接文档**：仅核实文档与当前客服代码、文档规范、独立交付价值和过期状态。保留价值时最小更新并做文档检查后合并；已被替代则记录替代位置并关闭 Draft。不触发完整业务 E2E。

## 合并门禁和集成验收

每次合并前重新 fetch；核对 origin/main、PR HEAD/base、干净任务工作树、冲突与迁移顺序、Review 全部解决、当前 HEAD CI/Required Checks/Skipped jobs；执行变更对应 T0/T1/T2 和必要 T3；无 P0、未授权风险或必需外部阻断后使用仓库允许的正常合并方式。记录 merge SHA，并更新其余分支基线。禁止强行合并失败/仍为 Draft 的 PR，禁止用旧 SHA 绿灯替代当前 HEAD。

已就绪时优先按 #73 → #72 Follow-up → #70 Follow-up，RAG #66 → #52；#69 依其真实契约兼容情况单独入队。#50/#60按核实结果关闭或最小合并。

全部可合并项结束后，按 MAIN-01～MAIN-34 分别记录 PASS/FAIL/BLOCKED/NOT VERIFIED，覆盖 APISIX/FastAPI/三端/PostgreSQL/Redis/Celery/健康观测、主助手、旅游正式/草案/分享/ICS、客服授权/确认/CAS/退款并发、RAG终态/授权检索、模型健康及账本。仅做实际可执行、隔离合规的最小集成回归；不以部分外部阻断挡住已合并项。

## 最终交付

按用户规定的 A 已修复合并、B 已施工未合并、C main 集成状态、D 下一轮施工范围四部分交付。每个 PR 给最终 HEAD、Merge SHA、修复摘要、实际验收证据；逐项列真实阻断和所需外部操作。注明 main SHA、迁移/回滚、已知风险、是否具备上线条件。状态仅使用 MERGED / MERGE_READY / BLOCKED / FAILED / NOT VERIFIED，并附证据。
