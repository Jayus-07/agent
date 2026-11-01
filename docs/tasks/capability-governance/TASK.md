# 任务：平台能力治理优化（capability-governance · G1–G6，供执行会话接手）

- 任务 ID：`capability-governance`
- 来源：cs-workstation-v2 独立审查（REVIEW.md §7/§8）+ 2026-11-01 治理对话决策——业界实践对照后确定五项优化，按「平台小改先行 → 客服试点 → 协议化 → 报表验证」排期
- Worktree：`D:\Program Files\workplace\agent-gov`（**新建**，自 main 创建；命令见 §8。cs-v2 的 agent-cs-v2 worktree 停靠 standby/origin-main，本任务不复用不清理）
- 分支策略：每期独立分支 `feat/cap-gov-gN`（自最新 main），独立 PR 合并 main；**合并前显式逐项确认全部 CI 结论**
- 状态：**计划已落稿，等待执行会话接手**（启动清单见 §8）

---

## 1. 背景与决策依据

### 1.1 治理面现状核验结论（2026-11-01，live + 代码 + 测试三层，全部证据已核）

- 客服域以**域图**形态被治理（capabilities.yaml domains 段声明），不经主图 Skill/Tool 注册表——架构明文设计（tool-skill-guide.md §Domain Graph），**非漂移**；
- 平台治理面健康：capabilities 对账零漂移（17 capability/14 routed/12 skills，registered 全 True）、/admin/tools 40 工具含 lock_git_sha、MCP 能力中心 3 连接 0 动态工具 0 派生错误、客服档案 RBAC 端点 200；
- 守护测试基线：治理批 **37 passed**（test_layer_consistency + test_admin_mcp_api + test_admin_tool_failure_probe + test_mcp_health_monitor）。

### 1.2 业界实践对照（2026-11 检索）与本任务定位

| 业界做法 | 本仓现状 | 本任务动作 |
|---|---|---|
| 每能力有 owner/sponsor（Agent 365/Entra 模式） | capabilities.yaml 无 owner 字段 | **G1** |
| 运行时漂移检测（幻觉调用/失败率突增告警） | stats 数据已有、缺告警规则 | **G2** |
| MCP 防"影子服务器"（allowlist 双保险） | 能力中心已拒未登记连接，缺网络出口白名单 | **G3** |
| 能力目录运营化（意图 × 调用量/转人工率/未解决率闭环） | 数据面已建（P7），缺管理端视图；主图已有参照、客服当试点 | **G4→G5** |
| 成本按能力归因 | llm_usage 无 capability 列，但 trace_records 有 workflow_name → join 报表可行 | **G6** |
| 框架之上单一治理层 | admin 三平面统一门禁+幂等 | 已达标，不动 |

按域角色：**企业助手（主图）**=治理最成熟域，是 G5 协议化的参照实现（零改动）；**客服域**=G4 试点第一用户（P7 数据面最厚）；**旅游域**=G5 第二个消费者（小适配器）。

### 1.3 非目标

不改任何业务行为；不动统一 LLM 链路 schema（G6 先 join 报表，验证失败升级用户决策）；不做客服域私搭平行实现（红线：治理面改动全域受益）；不冻结在途工作（travel-v2 生产化等照常）。

---

## 2. 已知信息（执行会话免重探，全部 2026-11-01 实证）

### 2.1 基线与工作树

- 基线：**origin/main@16378db**（PR #195 合并点）；本任务预计**零 DB 迁移**，若有新增迁移按 129+ 顺延并登记 init_db + 迁移守卫 NON_AI（当前最新 129_travel 模板种子）；
- cs-v2 worktree `agent-cs-v2`：停靠 `standby/origin-main`@16378db、干净，**留给审查/备用，本任务不用**；
- 新 worktree 命令（主仓库执行）：`git worktree add "D:\Program Files\workplace\agent-gov" -b feat/cap-gov-g1 origin/main`（G1 起各期在此内自最新 main 建分支）；
- worktree 无 .venv：跑测试用主仓库解释器 `D:\Program Files\workplace\agent\.venv\Scripts\python.exe`（cwd 在 worktree 根，backend.* 从 worktree 导入）。

### 2.2 治理面代码事实（文件:行号级）

- `backend/orchestration/router/capabilities.yaml`：唯一声明处；结构 = `domains` 段（粗分类器事实源，customer_service/travel/selection_funnel 为域图，无主图 capability，:50 注释）+ `capabilities` 段（17 条，14 routed）；字段语义注释 :53-57（domain 归属 fail-fast、risk_level HIGH 永不 fast path、fast_path 白名单）；
- `backend/orchestration/router/manifest.py`：`load_manifest()` 启动期 fail-fast（`ManifestError`，:186-195；domain 归属违例校验 :73）；**G1 加 owner 校验就在这里，yaml 与校验必须同 PR 原子合入**（fail-fast 下先合校验会打挂启动）；
- 守护测试四件：`test_registry_consistency.py`（清单↔注册表双向守护）+ `test_layer_consistency.py`（分层路由）+ `test_consistency_report.py` + `test_adr0001_dual_registry_merge.py`；
- `/capabilities` 只读 API：`backend/app/api/routes/capabilities.py`（无 admin 门禁，只读；挂载 router.py:110，B13 管理端 /skills 页数据源）；live 实况 count=17 routed=14 skills=12、registered 全 True、workflows=daily_report/inventory_alert/market_research/selection_decision；
- `/admin/tools`：`admin_tools.py`（:41 prefix、:334 stats / :425 list / :458 constraints / :491 errors）；live count=40、含 lock_git_sha；stats 已有 top_failed_tools/top_error_classes/skill_failures（G2 的数据链现状）；
- `/admin/mcp`：`admin_mcp.py`（:52 prefix，**全部端点 require_admin_user + 写操作 Idempotency-Key**，:19 注释）；GET 端点 :573 overview / :594 connections / :1323 tools / :1940 health / :2010 export；live overview：loaded=true、connection_count=3、active_tools=0、grant_count=0、derive_errors=[]；动态 skill 注册表 `backend/skills/mcp_dynamic/`；
- 客服档案：`rbac.py:1150` GET `/sys/rbac/customer-service/team`（**require_rbac_admin 门禁**，注意前缀无 /api；管理端 BFF 剥 /api）、:1235 PATCH；管理端 `frontend-admin/src/api/rbac.ts:251/266` + settings/access 页消费；
- 归因数据面：`ai.trace_records`（020 迁移）**有 workflow_name 列** + data JSONB；`llm_usage`（013 迁移）有 trace_id/tenant/component（llm|embedding|rerank）/model/cost_usd，**无 capability 列** → 按域归因 = `llm_usage ⋈ trace_records ON trace_id GROUP BY workflow_name`，零 schema 改动；
- P7 数据面（G4 数据源）：意图分布 = messages join conversations 收口租户（`cs_dashboard.py` 可复用 SQL）；未解决 = `ai.unanswered_questions`（070 迁移，question_hash 聚类）；客服域意图清单 = `backend/customer_service/router/intents.py`（24 意图）+ fine_router；
- 管理端页面：/skills /tools /mcp /approvals /cost-governance /settings-access 等已存在；frontend-design-standard 约束新页。

### 2.3 身份与测试口径

- REST 身份：`X-API-Key`（值 `docker exec agent-app-1 printenv API_KEY`，**不回显**）+ `X-User-Id`/`X-Tenant-Id`/`X-Auth-Type: jwt` + **`X-User-Roles: admin`**（require_admin_user 角色来源，backend/config/auth.py:33）；直连 :8000；
- 测试：仓库根 `python -m pytest backend/tests/... -q --no-cov`；真库 5433；治理批基线 **37 passed**（G 期验收 = 只增不减）；frontend-admin vitest 基线由执行会话首跑记录；
- 中文请求体一律 Python http.client/urllib（Git Bash curl 会 GBK 422）。

### 2.4 环境坑（本会话实证，勿再踩）

1. **`devctl.bat rebuild` 在 Git Bash 管道下静默失效**（确认提示读 EOF → exit 0 零输出零效果）——重建用等价原命令：`COMPOSE_PROJECT_NAME=agent docker compose -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.local.yml build <services> && up -d <services>`，完成后 **grep 容器内代码标记** 验证真的换血；
2. **Mimosa hook 拦截 Bash 直写文件**（含个别读命令误判）——写文件一律 Write/Edit 工具；
3. **GitHub 直连间歇抖动**——push/PR 操作用「失败 30s 退避 ×5」窗口重试；**注意管道 `cmd | tail && break` 会吞退出码**，用 `cmd > log 2>&1; rc=$?` 判定；
4. **vpnkit 半开病态**（pytest.ini 记载）：宿主→docker PG 握手可能 select 无限挂等——测试内 psycopg/psycopg2 连接**必须带 connect_timeout**（PR #193 已修 4 个测试工厂，新增测试遵守）；pytest.ini 已启用 timeout=300/thread；
5. 并行会话全量 pytest 期间冻结不动代码/容器（先 `powershell Get-CimInstance` 查 python 进程命令行）；本会话曾遇并行重建共享栈——重建前 docker inspect 确认容器归属。

---

## 3. 总计划（G1–G6）

| 期 | 内容 | 改动面 | 依赖 | 预估 |
|---|---|---|---|---|
| G1 | capabilities.yaml + domains 段加 `owner` 字段（manifest fail-fast 校验 + 对账断言 + /capabilities 返回 + /skills 页列） | manifest.py / capabilities.yaml / capabilities.py / frontend-admin /skills | 无（**开工先拿 owner 映射**） | ≤1 天 |
| G2 | 运行时对账告警：未注册工具调用指标 + 工具失败率环比突增告警规则 | 指标埋点（探明后定）+ Grafana provisioning 规则 | 无 | ≤1 天 |
| G3 | MCP 出口白名单：connect 前置 allowlist 校验 + 部署注记 | admin_mcp/mcp 动态链路一处 + README 注记 | 无 | ≤1 天 |
| G4 | 客服意图目录试点：`GET /cs/capabilities/inventory`（意图 × 调用/转人工/未解决）+ 管理端只读视图 | cs 域新只读端点 + frontend-admin 新视图 | 无（可与 G1-G3 并行） | 1-2 天 |
| G5 | 域清单协议化：约定文档 + travel 适配器评估/最小实现 | tool-skill-guide.md（+contracts 如涉及）+ travel 域适配器 | G4 | ≤1 天 |
| G6 | 成本按域归因报表：join 验证 → /admin 报表（或 cost-governance 页扩展） | 只读报表端点/页 | 无（建议最后） | 1-2 天 |

节奏：G1/G2/G3 可并行先行；G4 并行试点；G5 跟进 G4；G6 收尾。每期独立 PR，合入后回填 PROGRESS。

---

## 4. 分期详情与验收

### G1 owner 字段（每能力有负责人）

- **开工第一件事**：向用户索取「capability/domain → 团队」owner 映射（17 capabilities + domains 段全部条目）；未拿到前不得动 yaml（fail-fast 语义下 yaml 与校验原子合入）；
- 改动：①capabilities.yaml 每条加 `owner: <团队标识>`；②manifest.py 结构校验加 owner（非空字符串，缺失/空白 fail-fast）；③capabilities.py 返回 owner；④frontend-admin /skills 页加「负责人」列；
- 测试：test_registry_consistency 加断言（全部条目 owner 非空）；manifest fail-fast 用例（缺 owner 启动拒绝）；管理端列展示断言；
- **验收**：live /capabilities 每条含 owner；对账零漂移；治理批 37 passed 不回归；CI 显式全绿。

### G2 运行时对账告警

- 第一步（探明再改）：读工具执行器对**未注册工具名**的现行行为与失败分类；确认 stats 聚合链（admin_tools stats 的数据源表/指标）；
- 增量（最窄路径，探明后定）：①`tool_unregistered_total` 类指标埋点（执行器拒绝点）；②Grafana provisioning 规则文件入仓：未注册调用 >0 告警 + top_failed_tools 失败率环比突增告警；
- **验收**：本机触发一次未注册调用 → 指标可见 → 规则命中（截图/查询证据）；既有治理批不回归。

### G3 MCP 出口白名单

- 第一步（探明再改）：读 admin_mcp 连接创建流程——未登记 host 的 connect 请求现行行为（能力中心审核流是否已前置拒绝）+ mcp-service 容器实际出口路径；
- 增量（最窄路径）：①应用层 connect 前置 allowlist 校验（allowlist=已批准连接集合/配置项）；②部署/prod-4c8g/README.md 补网络出口注记（与 PR #195 的客服开关注记同段风格）；
- **验收**：T2——未登记 host 连接被拒（结构化错误）+ 已登记 host 正常；test_admin_mcp_api 全绿；live overview derive_errors 仍为空。

### G4 客服意图目录试点（运营视图）

- 端点：`GET /cs/capabilities/inventory`（形状按 G5 协议预期设计：`[{intent, description, stats:{calls, handoff_rate, unresolved}}]`）——数据源复用 cs_dashboard 的意图分布 SQL + unanswered_questions；权限对齐 P7 口径（**supervisor 全量**，agent 仅本人？→ 意图目录是运营视图，建议 supervisor-only，试点的验收里写明）；租户闸必须收口；
- 管理端：frontend-admin 只读视图（/skills 页扩展 tab 或独立页，倾向独立页挂治理菜单）；遵循 frontend-design-standard；
- 测试：T2（supervisor 200/agent 403/跨租户 0 串数/数值与构造数据比对）+ 管理端 page.test；
- **验收**：live 数值与 DB 抽查一致；意图条数 = intents.py 实况（24）；page.test 绿。

### G5 域清单协议化

- 约定文档落 tool-skill-guide.md：域图 inventory 端点形状/权限/数据口径（让 travel/未来域照抄）；涉及跨层契约则同步 cross-cutting-contracts.md；
- travel 适配器：静态意图清单可直接导出（travel prefilter）；**运行时统计依赖 travel 数据面（当前无 dashboard）——诚实标注「统计列待 travel-v2 数据面」，先交付静态清单 + 协议合规**；
- 主图：零改动即满足约定（capabilities 即主图清单）；如形状有差异，注明映射；
- **验收**：约定文档合入；travel 适配器最小实现 + 测试；主图/客服不回归。

### G6 成本按域归因报表

- 第一步验证：抽样 SQL `llm_usage ⋈ trace_records ON trace_id GROUP BY workflow_name`——验证 join 覆盖率（llm_usage 行有 trace_id 且命中的比例）与数值合理性；
- 报表：优先扩展现有只读统计（/admin/tools/stats 同层新增按域聚合段）或 cost-governance 页（先探明该页数据源再定）；**零 schema 改动**；若 join 覆盖率不足需动统一链路 → **停下升级用户决策**，不得擅自加列；
- **验收**：报表按域数值与 DB 抽查一致（构造已知用量比对）；只读端点权限对齐 /admin 面。

---

## 5. 技术约定（执行会话必须遵守）

- **唯一事实源红线**：capabilities.yaml/manifest/registry 是唯一声明处，禁止代码手写映射、禁止客服/旅游域私搭平行实现；治理面改动一处全域受益；
- **零迁移目标**：G1-G6 预计无 DB 迁移；实际需要时新编号顺延 + init_db 登记 + 迁移守卫 NON_AI 归类（创建即登记）+ 可回滚；
- 契约/事件只追加；管理端写路径（如有）必须幂等键；本任务端点全部只读（G2 指标埋点除外）；
- 每期：实现 → 测试 → 向用户出示 diff+验证 → 确认后提交（双重路径限定）→ PR → CI 显式逐项全绿 → 合并 → PROGRESS 回填；
- 交付记录：命令、PASS/FAIL、耗时、未运行范围；未实际执行不得报通过。

## 6. 测试策略

- 守护断言优先加进既有文件（test_registry_consistency / test_layer_consistency），端点行为新建 T2；管理端 page.test 随页交付；
- 回归面：治理批 37 passed 基线 + backend/tests/api admin 系测试 + frontend-admin vitest（首跑记基线）；
- 真库测试 p9g- 前缀用后即清；遵循测试分层（testing-guide.md）。

## 7. 总验收标准

1. G1–G6 全部合入，逐期验收矩阵打勾（PROGRESS 回填证据）；
2. 治理面对账零漂移：registered 全 True + owner 全非空 + live API 实测；
3. 守护测试只增不减（37 passed 基线）+ 新增断言入 CI 生效；
4. G2/G3 的告警与拒绝行为有触发证据（非仅代码存在）；
5. G4/G6 报表数值与 DB 抽查一致；
6. 文档同步：tool-skill-guide.md（owner 约定 + 域清单协议）、frontend-guide.md（管理端新视图）、cross-cutting-contracts.md（如涉契约）、部署 README（如 G3 涉网络）；
7. 零迁移（或迁移合规登记）；主链路（/agent、客服、旅游）冒烟 200 零回归。

## 8. 会话交接（执行会话启动清单）

1. 新建 worktree：主仓库执行 `git worktree add "D:\Program Files\workplace\agent-gov" -b feat/cap-gov-g1 origin/main`；`git status` + `git log --oneline -3` 核对（预期基线 ≥16378db，若 main 前移先 rebase 对齐）；
2. 通读本 TASK + 同目录 PROGRESS.md + `docs/tasks/cs-workstation-v2/REVIEW.md` §7/§8；
3. **G1 阻塞点**：向用户索取 owner 映射（或授权占位 `platform-team` + 待替换标注）后再动 yaml；
4. 每期按 §5 流程；遇 §2.4 环境坑按预案处理；
5. 暂停时按仓库规范记录 PAUSED 现场（HEAD/未提交/测试证据/下一步）。

## 9. 决策点与风险

| 决策点/风险 | 处置 |
|---|---|
| G1 owner 映射需用户输入 | 开工第一问；未获前只做校验层与测试骨架 |
| G2/G3 现状可能与预期不符 | 先探明再改（§4 已写探明步骤），最窄增量路径以探明结果为准 |
| G5 travel 运行时统计缺数据面 | 验收降级路径已写明（静态清单先行），不造假数据 |
| G6 join 覆盖率不足 | 停下升级用户决策，不擅自加列/动统一链路 |
| 并行会话干扰 | 全量 pytest 冻结纪律 + 重建前 docker inspect 查归属（§2.4） |
