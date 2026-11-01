# PROGRESS — capability-governance（平台能力治理优化）

- 任务文件：同目录 TASK.md（唯一事实源）；治理面复查证据：`docs/tasks/cs-workstation-v2/REVIEW.md` §7/§8
- 更新时间：2026-11-01
- 当前状态：**G1–G6 全部完成并合入 main**（任务关闭；收尾总验收见日志 2026-11-01（三）§总验收七条核对）

## 阶段状态总表

| 期 | 主题 | 状态 | 分支 / PR | 证据 |
|---|---|---|---|---|
| G1 | capabilities/domains 加 owner 字段（fail-fast + 对账 + 管理端列） | **已完成** | feat/cap-gov-g1 / #198 @180284c | 日志（二）；live owner 17/17 |
| G2 | 运行时对账告警（未注册工具指标 + 失败率突增规则） | **已完成** | feat/cap-gov-g2 / #200 @a204d02 | 日志（三）；promtool 4 场景 + live 规则 health ok |
| G3 | MCP 出口白名单（connect 前置 allowlist + 部署注记） | **已完成** | feat/cap-gov-g3 / #202 @adf6adc | 日志（三）；live 拒绝证据 |
| G4 | 客服意图目录试点（inventory 端点 + 管理端只读视图） | **已完成** | feat/cap-gov-g4 / #203 @4702018 | 日志（三）；live total=25 |
| G5 | 域清单协议化（tool-skill-guide 约定 + travel 适配器） | **已完成** | feat/cap-gov-g5 / #204 @41c29de | 日志（三）；协议节 + 适配器 3 tests |
| G6 | 成本按域归因报表（join 验证 → 只读报表） | **已完成** | feat/cap-gov-g6 / #207 @af6dfa7 | 日志（三）；live DB 抽查一致 |

状态取值：计划中 / 进行中 / 待验收 / 已完成 / BLOCKED（注明原因）/ PAUSED（注明现场）。

每期完成后必须回填：分支与 PR、实际测试命令与 PASS/FAIL、live 验证证据、验收矩阵对应项、遗留事项。

## 环境

- 基线：origin/main@16378db（PR #195 合并点）→ 任务收官时 main 已前移至 af6dfa7（含并行会话 #201/#205/#206 等正常演进）；执行 worktree `D:\Program Files\workplace\agent-gov`
- 备用：`agent-cs-v2` worktree 停靠 standby/origin-main（审查会话遗留，本任务未动）
- 测试解释器：`D:\Program Files\workplace\agent\.venv\Scripts\python.exe`（worktree 无 .venv）；真库 5433；worktree .env 从主仓库拷贝
- frontend-admin node_modules：Windows junction 复用主仓库依赖（依赖输入未变不重装）
- 治理测试基线：治理批 37 passed——本任务结束时相关面 56 passed（+registry_consistency 19）只增不减
- live 口径：X-API-Key（容器 printenv，不回显）+ X-User-Id/X-Tenant-Id/X-Auth-Type: jwt/X-User-Roles: admin → :8000
- 环境坑与预案：TASK §2.4（devctl 静默失效 / Mimosa hook / GitHub 重试 / vpnkit connect_timeout / 冻结纪律）——全部按预案处理过

## 日志

### 2026-11-01（四）—— 收尾销账：owner 归属确认 + G5 翻正连带项移交（执行会话续）

- **owner 归属确认**（用户答复「团队目前没有区分多团队」）：单一平台团队，
  `platform-team` 即**实际归属**而非占位——27 条值不变，capabilities.yaml
  头部标注由「待按团队实况替换」改为现状声明（多团队化时再按域拆分）。
  主图（企业助手）覆盖说明：capabilities.yaml 即主图能力清单，17 条
  capability + 10 个域（含三个域图域）均已带 owner；MCP 动态能力不经
  yaml，归属由能力中心审核/授权治理承接（单团队下无影响）。
- **G5 翻正连带项移交 travel-v2**：写入
  `docs/tasks/travel-v2-production/TASK.md` 阶段四「接线与数据源」E5——
  数据面（意图级打点）落地时连带改 `travel_inventory.py`（聚合 SQL 照抄
  cs_inventory 模式）+ 翻 `stats_available` + 测试改数值比对；协议形状
  不变，聚合不到的维度保持 null。capability-governance 侧遗留清单同步
  销账。

### 2026-11-01（三）—— G2–G6 实施与合入 + 任务关闭（执行会话续）

逐期交付（全部独立 PR + CI 显式逐项全绿后合并；skipping 项核对均为非 RAG 变更预期，paths 过滤未触发的 workflow 亦核对属预期）：

- **G2**（#200 @a204d02）：探明修正——执行器对未注册 tool_key 不拒绝（get_policy 兜底 DEFAULT_POLICY），记账点定在 SafeToolExecutor.run 单点（覆盖全部路径）；告警事实源为 Prometheus 端 prometheus-alert-rules.yml（非 Grafana provisioning），已有 ToolFailureSpike 绝对阈值，本次补 ToolUnregisteredCallDetected（increase>0）+ ToolFailureRateSuddenJump（环比 >2× 基线且 >2%、有流量）。验证：T2 3 passed（执行链记账/已注册不计/probe 过滤）；promtool check 68 rules SUCCESS + **test rules 4 场景 SUCCESS（入仓 prometheus-alert-rules.test.yml 可复现触发证据）**；prometheus 实载两规则 health ok（observability profile 单容器临时起停，/api/v1/rules 查询证据，验证后清理恢复原状）；app 抓取 target up。如实声明：主进程端到端触发无自然入口（正常形态下 capability 全注册——该缺口正是告警要抓的场景），规则命中证据以入仓 unit test 承接。
- **G3**（#202 @adf6adc）：探明修正——SSRF（url_guard）只拦私网/非 http，公网任意 host 可建连接；mcp-service 的 MCP_ALLOWED_HOSTS 是入站防护。增量：`MCP_EGRESS_ALLOWLIST`（精确 host/*.suffix 通配，**未配置=不启用**，历史行为零变化）+ `_validate_egress_allowlist` 前置于 create/update（update 仅 URL 变化时校验，存量不追溯）+ 部署 README 注记。测试 test_admin_mcp_api 20 passed（+5）；**live 拒绝证据**：临时配置 allowlist 重建 app 后 POST 公网 IP 未登记 host → 400「MCP 出口白名单未放行该 host: 8.8.8.8（已配置 2 条模式）」→ 撤销临时配置重建恢复默认态（容器内 env 未设复核）。
- **G4**（#203 @4702018）：`GET /cs/capabilities/inventory`（G5 协议预期形状）+ 管理端 `/capability-inventory` 独立只读页（能力治理菜单组；`/cs-*` 前缀被 navConfig 既有守卫正确拦截——客服端命名空间，改通用名留 G5 多域位）。意图全集 = FINE_INTENTS 唯一事实源（**25 条实况**，TASK 预估 24 以代码为准）；权限 supervisor-only（cs supervisor 或平台管理员）——require_agent 增向后兼容 `allow_unbound_admin` 透传（重派端点同款语义，消费方回归 27 passed）。测试：T2 4 passed（agent 403/全集 25/非法 range 422/构造数据三元组逐项比对+他租户零串数，p9g- 前缀用后即清）；frontend-admin 84 files/584 passed（降并发跑；默认并发 3 例为全 src 扫描 5s 超时**资源型失败**，单文件全绿）；tsc 0 错。live：admin 200 total=25、viewer 403、页面 200。
- **G5**（#204 @41c29de）：tool-skill-guide.md 新节「域清单协议」（形状/权限/意图清单唯一事实源/统计诚实降级/主图映射=capability↔intent）；travel 适配器 `GET /travel/capabilities/inventory`（TravelIntent 全集 9 + INTENT_DESCRIPTIONS 与枚举同文件声明；无数据面按协议降级 `stats_available=false`——**统计列待 travel-v2 数据面，诚实标注不造假**）；主图零改动即合规。测试 3 passed。文档同步判定：cross-cutting-contracts.md（域内运营视图形状，未涉跨层契约）与 frontend-guide.md（行为规范非页面台账，新页面无新契约）均无需修改。
- **G6**（#207 @af6dfa7）：**探明修正——正确 join 伙伴是 trace_summary（013 同源带 workflow_name），非 TASK §2.2 记载的 ai.trace_records**（020 新链路与 llm_usage.trace_id 实测零相交）。全库验证：35483 行命中（45.7%），llm 组件占成本 97%（$76.25/$78.48）其 join 覆盖 72%——**未触发 §9「停下升级」条件**（join 可行、零 schema 改动、未动统一链路/未加列）。端点 `GET /admin/tools/usage-by-domain?days=1..90`（require_editor_user 对齐 stats 同层；coverage 段显式披露 join_partner/coverage_ratio 不伪造 100%）。测试 2 passed（构造行三元组逐项比对+coverage 如实披露+editor 403）；**live DB 抽查比对：agent 域 API rows=631 == DB rows=631、cost 逐项一致**。

**总验收七条核对**（TASK §7）：
1. G1–G6 全部合入，逐期验收矩阵回填（本表+本日志）✅
2. 治理面对账零漂移：G1 live /capabilities owner 17/17 非空、count=17/routed=14/skills=12 与基线一致；registered 全 True（registry_overview 断言持续生效）✅
3. 守护测试只增不减：治理批 37 基线 → 相关面 56 passed；新增 TestManifestOwnership/TestEgressAllowlist/未注册指标/G4 T2/G6 T2 全部入 CI ✅
4. G2/G3 告警与拒绝有触发证据：G2 promtool unit test 4 场景（入仓可复现）+ live 规则加载 health ok；G3 live 结构化 400 拒绝证据（临时配置验证后恢复默认态）✅
5. G4/G6 报表数值与 DB 抽查一致：G4 live total=25 = intents.py 实况 + T2 构造比对；G6 live DB 抽查 rows/cost 逐项一致 ✅
6. 文档同步：tool-skill-guide.md（owner 约定 + 域清单协议两节）、部署 README（G3 出口白名单注记）、grafana-guide.md（G2 规则测试资产指针）；frontend-guide/cross-cutting-contracts 判定无需改（理由见 G5）✅
7. 零迁移（全程未新增 DB migration）；主链路冒烟每期重建后 :8000/:9080/:3200/:3100 全 200，终验见下方终验记录 ✅

**遗留与移交**：
- owner 归属已确认（日志（四））：单一平台团队，`platform-team` 即实际归属（非占位）；未来多团队化时按域拆分（独立小 PR，yaml 头部标注同步改为现状声明）；
- G4/G6 的管理端前端面板为数据表/汇总卡级 MVP，可视化增强（图表/下钻）未在本任务范围；
- travel-v2 数据面补齐后：G5 适配器翻正 `stats_available` + G5 协议未变；
- live overview derive_errors 曾见 8 条 govtest 孤儿快照——经 DB 时间戳核实为**并行会话活跃测试数据**（07:18 UTC 创建），非本任务产物，未清理（共享库他人活跃测试）；如后续仍在需该会话自行清理；
- 交接文档偏差记录：TASK §11「候选池」在 TASK 文件中不存在（实际 §1–§9），任务关闭以本文件状态承接；TASK §2.2 的 join 伙伴记载偏差已在 G6 修正并留档。

### 2026-11-01（二）—— G1 实施与合入（执行会话）

- **owner 映射决策**：用户授权统一占位 `platform-team`（27 条全部），yaml 头部标注「待按团队实况替换」，替换走独立小 PR。
- **交付**（PR #198，feat/cap-gov-g1 → main@180284c，10 文件 +125/-5）：
  - capabilities.yaml：domains(10)+capabilities(17) 每条 `owner: platform-team` + 头部 owner 约定注释；
  - manifest.py：DomainDecl/CapabilityDecl 加 owner 字段，`load_manifest()` 缺失/空白 fail-fast（与 yaml 同 PR 原子合入）；
  - /capabilities 每条返回 owner；frontend-admin /skills 能力清单加「负责人」列；
  - 测试：缺 owner 启动拒绝 ×2 + TestManifestOwnership 对账断言 + API owner 断言 + page.test 列断言；既有 manifest fixture（test_registry_consistency / test_layer_consistency）连带补 owner；
  - 文档：tool-skill-guide.md 快速接入清单第 4 条落 owner 约定。
- **验证记录**（全部实际执行）：
  - G1 定向（test_registry_consistency + test_registry_overview_api）28 passed；
  - 治理批四件套 37 passed（基线保持）；
  - frontend-admin vitest 83 files / 582 passed（首跑基线，含新增 1 例）、tsc --noEmit 0 错；
  - 容器 agent-app-1 / agent-frontend-admin-1 自 agent-gov worktree 重建（compose 等价原命令，非 devctl）healthy，容器内换血标记（yaml 27 / manifest 3 / api 1）全在位；
  - live 直连 :8000 `GET /capabilities`：200，owner 17/17 非空，count=17/routed=14/skills=12 与基线一致零漂移；
  - 主链路冒烟 :8000/health、:9080、:3200/login、:3100 全 200；
  - CI 逐项核对：RAG Smoke (8 cases) / Detect RAG changes / admin-observability-auth / frontend-admin-functional / frontend-admin-security / scan 全 pass；skipping ×2（RAG Smoke Evaluation real API、preflight）= 非 RAG 变更预期跳过。
- **环境备注**：worktree 无 .env → 从主仓库拷贝（真库 5433 指向正确）；frontend-admin node_modules 以 Windows junction 复用主仓库依赖（依赖输入未变）；测试窗口有并行会话 travel booking 定向 pytest（无 HTTP 依赖，零冲突）。
- 待办：G2 探明执行器未注册工具行为与 stats 数据链；G3 探明 admin_mcp 连接流程。

### 2026-11-01（一）—— 计划落稿（审查会话产出）

- 产出来源：cs-workstation-v2 九期独立审查收尾后的治理面复查（治理测试全绿 + 六份治理文档判定 + 两类测试基建缺口已修复并入 PR #193）；业界实践对照（Agent 365/Entra owner 模式、MCP allowlist/HITL/审计、能力目录运营化、运行时漂移检测）。
- 关键核验（免重探，详见 TASK §2）：capabilities 17/14 routed/skills 12 对账零漂移；admin tools 40+lock_git_sha；MCP 中心 3 连接 0 动态工具；trace_records 有 workflow_name（按域归因 join 可行）；客服域零游离 Tool/MCP。
- 用户决策记录：优化分五项、按「平台小改先行（G1/G2/G3）→ 客服试点（G4）→ 协议化（G5）→ 报表验证（G6）」排期；不冻结 travel-v2 生产化等在途工作。
- 待办：执行会话接手后从 G1 开始；G1 前置=owner 映射。
