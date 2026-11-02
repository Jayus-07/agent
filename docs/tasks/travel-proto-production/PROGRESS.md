# PROGRESS: travel-proto-production（原型生产化）

> 2026-11-01 初始化（计划会话）· 尚未开工 · 下一会话从 TASK.md §4 D0 侦察定稿开始

## 初始化侦察记录（2026-11-01，计划会话完成）

### 真实前端现状盘点（main@921baf9+#217/#219/#220 之后）

- **路由**：/travel（TravelHome）、/travel/chat（TravelChatPage）、/travel/discover、/travel/itineraries、/travel/itineraries/[id]（+edit）、/travel/templates（+[slug]）、/travel/share/[share_id]。
- **组件现状核实**：
  - TravelV2Map：leaflet 动态 import + 高德瓦片（W14 基础已在，差多源回退/琥珀标记细节）。
  - TravelHome：TravelTopBar/BottomNav 已挂载（G2/G3 骨架在）；无 Hero/头像抽屉（H1/H2/G4 缺）；快捷查询 grid/模板 carousel/PlanCard 已有（H3-H6 形态对齐即可）。
  - TravelSearchPanel：美食选入带「加入日期+用餐时段」选择器（W7 核心已有）；酒店卡挂 BookingConfirmGate；车次走 12306 灰度。
  - TravelV2EditDialog：menu/add/search/activity/time/move/transport 多视图与原型编辑面板同构（W8 基本对齐）。
  - TravelTripRevisionActions：版本级 ICS 下载（downloadTravelTripRevisionICSv2）+ 分享创建/复制（W9 核心）。
  - travelV2WriteState 三态/UNKNOWN 重试/CONFLICT 刷新（W10 齐全）。
- **结论**：真实平台功能骨架覆盖原型约七成（W/W9/L 系列多为「形态对齐」），真正的新建量集中在：**视觉体系（G1 设计令牌）、首页 Hero+抽屉（H1/H2/G4）、概览卡统计块（W2）、生成流迁入工作台（F1，最重）**。

### 关键判断（供执行会话参考）

1. 交互流差异是体验重构核心：原型「首页输入→进工作台全程对话伴随生成」vs 真实「chat 页生成→保存→跳工作台」。P4 前先与用户确认 §5 决策点 5。
2. 视觉体系是用户「不是我的原型图」感知的主要来源：G1 设计令牌先行（P1），否则逐页改样式会反复。
3. 原型 `data-test` 锚点（opt/book/tz/convbad/ics/dayops 等）+ 悬浮窗场景按钮可作为点测驱动；HANDOFF §2 坑清单（可见性/tab.id/轮询断言）必读。
4. 原型 CSS 是自定义体系（~489→577 条规则基线），映射到 Tailwind 时以「设计令牌 + 逐组件类名」方式转换，不整段搬运 style 块。

### 环境备忘

- Worktree：`D:\Program Files\workplace\agent-wt-travel-proto`；frontend/node_modules Junction 指主仓库（已建，vitest 直接可跑）。
- 测试命令：`cd frontend && ./node_modules/.bin/vitest run src/components/travel src/api`；`./node_modules/.bin/tsc --noEmit`（16 错基线）。
- 后端零改动预期；共享栈 rebuild 用 `devctl.bat rebuild /y`（PowerShell 调用）；PG 一律 127.0.0.1:5433。
- rollinggo live 链路与跳转预订已上线（travel-v2-production 任务，见 docs/tasks/travel-v2-production/PROGRESS.md）——本任务前端重构不得破坏其前端消费面（AutoOptimizePanel/BookingConfirmGate/跳转预订链接）。

## 阻塞 / 下一步

- **待用户确认 TASK.md §5 六个决策点**（推荐项已给）→ 确认后执行会话从 D0 开始。
- D0 产出：差距矩阵 v1（锚点级）+ 决策结论回填本文件。

## 决策点更新（2026-11-01，用户回复后）

- **决策点 5 已定**：/travel/chat 独立页废弃，生成流全部迁入工作台（P4 执行时含旧链接重定向与测试迁移）。
- **决策点 6 事实修正**：本机 `TRAIN_MCP_ENABLED=true`、mcp-12306 为共享栈正式服务（healthy），运行态验证福州→厦门真实车次返回（D3333 等 3 趟）——**车次 Tab 当前可用**，此前「默认关」表述有误。「灰度」依据 = 上游 12306 非官方聚合无 SLA（train.py docstring），是部署策略不是本机现状。
- **12306 管理端配置现状**：走静态 Tool 直连通道（travel.train.search ToolSpec → 容器内直连 mcp-12306:8000），不经管理端能力中心；管理端仅有测试残留 12306-govtest（disabled），无正式连接。可选子项（用户定）：(a) 维持静态通道（推荐）；(b) 登记能力中心改走动态 capability；(c) 清理 govtest 残留。
- **仍待确认**：决策点 1/2/3/4（推荐项已给，执行会话开工前问询）。

### 运行态证据
- `search_trains(福州→厦门, +14天)` → count=3，D3333 06:43→08:20（agent-app 容器内验证，2026-11-01）。

---

# 总计划全景（2026-11-02 持续实施后接力更新——下一会话以此为准）

## 一、总计划与进度

| 阶段 | 内容 | 状态 | 产出/PR |
|---|---|---|---|
| 前置任务 travel-v2-production | P1–P5：rollinggo live 接线、F18 card、F04/F02/F05、F17 布局+原型对齐、跳转型预订 | ✅ 全部合并 | #205–#220 |
| 计划定稿 + D0 侦察定稿 | 差距矩阵 v1（锚点级，#220 后重核：首页/列表页/骨架大半已对齐）+ 决策点 1–6 全定稿 | ✅ 合并 | #222/#224/#235 |
| **T1 12306 迁管理端能力中心** | 动态 capability 通道（动态在场优先→撤销 fail-closed→静态回退→disabled 如实）+ 治理登记/黄金证据 + `TRAIN_MCP_ENABLED` 收窄 false | ✅ 合并 | #232 |
| **P1 全局骨架** | 底导「我的」接抽屉（共享组件向后兼容扩展）+ 旅游导航插槽（决策 4）；G1 令牌决策=按 travelTheme 增量收口不建第二套表 | ✅ 合并 | #236 |
| **P3 工作台功能块** | W2 概览统计块（六行，travelOverview 单一事实源）+ W3 天数 chip 主题；W7/W11 实测已对齐零改动 | ✅ 合并 | #240 |
| **P4a 生成流引擎抽取 + 工作台生成态** | `useTravelPlanGeneration` 单一实现 + `/travel/itineraries/new` 三列生成态（草稿概览/地图+生成面板+对话伴随）+ 后端冲突消息可读性修复 | ✅ 合并 | #246 |
| **P4b 入口切换 + chat 废弃**（分支 `feat/travel-proto-p4b-entry-switch`） | ① 首页输入卡 `action="/travel/chat"` → `/travel/itineraries/new`；② prompt 胶囊 href 同改；③ `/travel/chat` 路由改服务端重定向（保留 query：`trip_id` → 工作台 `travelV2WorkspaceHref`，其余 → `/travel/itineraries/new?prompt=…`，旧链接兼容）；④ 删除 TravelChatPage 组件与页面（工作台右列已内嵌 TravelV2TripConversation，移动端「对话」钮改指工作台）；⑤ 测试迁移：TravelChatPage.test 10 用例中仍有效的断言（UNKNOWN 同键重试/决策令牌持久化/澄清选项/tool.card）迁到 WorkbenchGenerate.test，其余随组件退役 | ✅ 已合并 | #254 + 决策 5 收口 |
| **T2 排程车程衔接修复**（后端确定性排程器） | 见下「T2 专项分析」 | ✅ 本地验收通过，待 PR | fix 5c78453；三城保存全链通过 |
| P2 首页对照校对 | H1–H7 逐屏对照原型点测，只修真差异 | ⬜ | 校对清单 |
| P5 历史/列表/地图形态校对 | W9 抽屉/时间线样式、L1 照片卡视觉、W14 invalidateSize 校正、W15 断点点测 | ⬜ | PR + L/W9/W14 全勾 |
| P6 总验收 | 原型全页面点测 + rebuild 全链（含生成→保存→跳转全链复验）+ 用户逐页验收 | ⬜ | 用户验收 |

**建议执行顺序**：P4b（前端最小收口决策 5）→ T2（planner 对齐，见下）→ P2/P5（校对类可穿插）→ P6（总验收，依赖 T2 落地后生成链路可用）。

### T2 专项分析：确定性排程器的车程衔接缺口（根因修正版）

**现象**：P4a 多轮真实生成后，保存被服务端以 `422 SCHEDULE_OVERLAP` 拒绝。相邻景点已取得 27 分钟车程，但下一站仍从上一站结束时刻开始。

**已定位根因**：`backend/travel/services/transit_service.py::schedule_day` 只将排程光标推进到 `cursor = end`，未计入已经生成的 `leg.minutes`。`start_time` 来自确定性排程器；LLM 的 `planning_agent.propose_day_plan` 负责候选分天，optimization_agent 无 LLM。因此旧提示词方案作废，不需要修改提示词、发布版本或 golden。

**修复**：将 leg 构造移到 start 计算前，`travel_minutes = max(0, int(est.get("minutes") or 0))`，`arrive_min = cursor + travel_minutes`，`start = max(arrive_min, open_min)`，`wait = start - arrive_min`。开门等待仅计算到达后的等待。首次排程、天气替换及 repair 均复用 `rebuild_days → schedule_day`，无需平行修复实现。

**validator 精确口径（只读未改）**：同天 items 按数组顺序，下一项 start 不得小于上一项 start + duration_min + 匹配 from/to 的 leg.duration_min；leg 仅在 reliability 为 verified/estimated 且 duration_min 非 None 时计入，无 start_time 的项跳过且不更新链条。前端映射 minutes>0 为 verified/estimated，其余 unavailable；排程对非正车程按 0，与校验器一致。SCHEDULE_OUT_OF_DAY、VERIFIED_CLOSED、VERIFIED_OPENING_HOURS 等既有硬门保持。

**回归证据（上会话）**：新增 `test_schedule_travel_buffer.py` 5 用例，修复前 4 红 1 绿、修复后全绿，包含直接调用 validator 本体验证排程产出；既有排程 73 passed；travel + travel_v2 定向回归 1569 passed、84 skipped、9 xfailed、零失败（39 分钟，既有门控与预期标记，本轮不重复运行）。

**收口验收**：重建后不同城市真实生成至少 3 次，全部经页面保存成功且自动进入工作台；validator、前端零改动。执行结果见本文件 T2 执行记录，未完成真机验证前不得标记验收通过。

## 二、工作树与环境

- **Worktree**：`D:\Program Files\workplace\agent-wt-travel-proto`（每阶段独立分支从最新 origin/main 建，独立 PR；当前分支 feat/travel-proto-t2-planner-schedule）。`frontend/node_modules` = Junction 指主仓库（vitest 直接可跑）。
- **共享栈**：`devctl.bat rebuild /y`（PowerShell 调用；`start/restart` 不换代码不换 .env）；改后端唯一生效路径 = rebuild。
- **PG**：一律显式 `127.0.0.1:5433`（5432 是宿主机另一套 PG，勿连）。
- **live 开关现状**：`TRAVEL_COMMERCE_PROVIDER_MODE=live` + `TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS=rollinggo.cn`（保持）；**`TRAIN_MCP_ENABLED=false`（T1 已收窄，动态通道在场优先，勿改回 true——改回会让撤销/停用静默回退静态直连）**。两份 .env（主仓库+worktree）均已同步。
- **治理运行态**：连接 rollinggo（agent 授权）+ 12306（两工具 approved+agent 授权，capability `mcp.t12306__query_tickets`/`_query_ticket_price`）；`12306-govtest` 与 probe-rest×3 为 disabled 残留（保留态，勿动）。
- **凭据纪律**：临时账号用完即停用+吊销（本任务已形成脚本化流程：注册→提权（仅治理操作需要）→用→UPDATE status=0+吊销 sessions+删 refresh_tokens）；凭据不落盘不入文档。
- **网络坑**：github.com:443 间歇不可达（api.github.com 走 gh 正常）——push/fetch 带重试循环即可；管理端 API 全路由要 `X-API-Key`（主仓库 .env），治理写操作还要 `Idempotency-Key` 头。

## 三、测试纪律（各阶段底线）

- 前端：`cd frontend && ./node_modules/.bin/vitest run src/components/travel src/api` → **P4b 后基线 417 passed**（423 − TravelChatPage.test 10 退役 + 迁入 WorkbenchGenerate.test 4，每阶段只增不劣化、零删断言；`src/app/travel` 另有 7 passed，含 P4b 新增 chat 重定向 6 用例）；`./node_modules/.bin/tsc --noEmit` → **16 错 = main 既有基线**（travel 面零错误）。
- 后端：按风险最小充分（本次 T2 触及确定性排程器；trip 契约测试在 `backend/tests/travel_v2/test_api_contract.py`）；跑测前查并发 pytest（Pitfalls §三）。
- 浏览器点测（IAB）：**rAF 在 IAB 被挂起**——依赖 `nextPaint` 的写状态两段式会停在 VALIDATING，先垫片 `window.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 16)` 再驱动流程；React 受控输入用 Playwright `fill()`（原生 value 赋值无效）；轮询断言非固定 sleep。
- 不跑全量 pytest/全量回归；提交前查并发会话。

## 四、验收总清单（P6 收口，T2 落地后执行）

1. 原型逐页面浏览器点测全 PASS（首页/生成态/工作台/模板/我的行程/编辑面板/历史/确认门/优选/查询四 Tab）；
2. 差距矩阵 v1 全项勾选回填 PROGRESS（排除项显式标注）；
3. 前端 423+tsc 基线全绿；后端触及面测试全绿 + rag_smoke CI 过；
4. `devctl.bat rebuild` 后运行态全链：登录→首页→**输入直达生成态→真实生成→保存成功→自动进工作台**（T2 验收的关键一段）→编辑→查询→优选→预订跳转/确认门→历史/ICS/分享；管理端治理视图（rollinggo+12306）与运行态一致；
5. `/travel/chat` 旧链接重定向兼容核对（带 trip_id 与带 prompt 两种）——**P4b 已真机核对通过（含裸访问），P6 复验**；
6. 移动端断点核对；已知边界如实标注（API 闭环预订 BLOCKED/机票契约位/天气契约位/notice-event 契约位）。

## 五、已知信息（执行会话开工前通读）

- **各阶段执行记录见本文件下方**（T1/P1/P3/P4a 四节，含全部坑与决策）；差距矩阵 v1 在 TASK §2；方案细节在 TASK §4/§4b；决策定稿在 TASK §5。
- **D0 头号发现**：#220 已把首页/列表页/骨架与原型双向对齐——校对类阶段（P2/P5）预期改动很小，先逐屏点测只修真差异。
- **P4a 关键决策**：生成引擎单例（hook）——P4b 删 TravelChatPage 时生成语义的回归网 = TravelWorkbenchGenerate.test + 迁移用例，勿在两处维护两份生成逻辑。
- 历史事故避坑（多会话并行/提交双路径限定/容器归属）：`.zcode/skills/agent-repo-pitfalls/SKILL.md` 必读。
- 原型点测：HANDOFF §2 坑清单 + data-test 锚点；`E:\.zcode\v2\visualizations\c052931e…\travel-platform-proto.html`（8124 静态服务）。

## T1 执行记录（2026-11-02，会话续工完成）

> 分支 `feat/travel-12306-dynamic-capability`（基于 main@7396c62 / PR #227 合并点）；改动未提交，待用户确认。

### 代码（5 文件）

- `backend/tools/travel/train.py`：抽出 `search_envelope_from_payload` / `price_envelope_from_payload`（四种实测契约的**单一解析事实源**，静态/动态通道共用）；静态封套 data 增标注 `channel: "static_direct"`。
- `backend/tools/travel/train_dynamic.py`（新建）：动态 capability 通道轻量 provider（参照 rollinggo_mcp 模式）——capability 解析（env 可覆盖 `TRAVEL_TRAIN_MCP_CAPABILITY`/`_PRICE_`）、三态判定 `train_channel()`、duck-typed 适配器（治理身份仍记 `travel_train_search_tool`/`travel_train_price_tool`，仅换数据源通道）。
- `backend/travel/services/live_search_service.py`：`search_trains`/`search_train_price` 通道选择（**消费方零改动**）；`_search_error_category` 补「未生效」→ disabled 类。
- `backend/tests/travel/test_train_dynamic_channel.py`（新建）：13 用例。
- `.github/workflows/rag_smoke.yml` + `.env.example`：定向清单登记 + 新配置项注释。

### 通道语义（关键设计决策，与 TASK §4b 的差异点）

1. **slug 实证**：连接名 `12306` 数字开头 → slugify 加 t 前缀，capability = `mcp.t12306__query_tickets` / `mcp.t12306__query_ticket_price`（计划初稿 `mcp.12306__*` 有误已修正；TASK §4b 已同步）。
2. **fail-closed 判别**：快照 connections 只含 enabled 连接，无法从快照区分「从未登记」与「整条连接被停用」。落地口径：
   - runtime 在场 → 动态（失败如实，**绝不回退静态**）；
   - 同址连接在场（base_url == TRAIN_MCP_BASE_URL，连接 id 为随机 uuid 不可推导）但工具 runtime 缺席（未审批/撤销/漂移）→ **如实拒绝不回退**；
   - 快照无同址连接（从未登记 或 连接被停用）→ 按 `TRAIN_MCP_ENABLED` 回退静态/如实未启用。
   - **运行态收口建议（待用户确认，未擅自改）**：本机 .env `TRAIN_MCP_ENABLED=true` 改为 `false`——语义收窄为「静态回退通道开关」后，连接停用等所有 runtime 缺席状态都如实拒绝（彻底 fail-closed）；未接入能力中心的部署保持 true 走静态（既有行为）。PROGRESS §2 旧的「勿回退」针对的是 T1 落地前其他会话勿动；T1 已落地，翻转属计划内收窄。
3. **授权边界**：mcp grants 门的是 agent 路由/治理面（test-invoke）；服务层内部数据通道不查 grants（与 rollinggo 同口径）——车次 Tab 是服务端数据集成不是 agent 能力调用。

### 测试证据

- 13/13 全绿（backend/tests/travel/test_train_dynamic_channel.py）：三态选择、configured_inactive fail-closed、动态执行失败不回退、四形态契约回放（成功/真无直达/坏站名/坏日期）、票价 data[] 同车次选取、车次/票价独立判定、静态通道回归（重构前后行为一致+channel 标注）；pg 用例为真 PG + mock FastMCP 12306 全链（登记→发现→审核→授权→服务层动态通道→撤销→denied→恢复）。
- 回归 41/41 全绿：test_train_mcp_tool / test_live_search_trace / test_auxiliary_tasks / test_tool_card_events。
- rag_smoke 定向清单已登记（CI 有 PG service + 迁移 114，pg 用例可真实执行）。

### 治理运行态（管理端登记，全部走 :9080 enforce 链路）

- 连接 `12306`（mcp_conn_eb2a8ea9d93d，connected，streamable_http/private/auth none）登记；discover 7 工具（其余 5 个保持 pending：search-stations/query-transfer/get-train-route-stations/get-train-no-by-train-code/get-current-time）。
- `query-tickets`(554) / `query-ticket-price`(555) approve（read/R1/无确认，domain 未分类）→ agent 授权（两工具）。
- **授权闭环黄金证据**：agent test-invoke 真实车次（福州→厦门 2026-11-16，**224 趟**，elapsed 1810ms）→ revoke 后 `product_capability_denied` → regrant 恢复成功。
- 治理视图对账：/admin/mcp/overview active_tools=3（rollinggo searchHotels + 12306×2）、grant_count=3，与库一致。

### rebuild 运行态验收

- `devctl.bat rebuild /y`（worktree 代码，钉住共享栈 agent）后 backend healthy。
- 车次 Tab API（POST /travel/v2/search/trains 福州→厦门）200 + 真实候选（source=12306，selection_id 形态 `12306:<hash>`）；**链账日志**：`[LiveSearch] train channel=mcp_dynamic capability=mcp.t12306__query_tickets` + `train_price channel=mcp_dynamic`（票价补全同走动态）。
- 域图 auxiliary query_train 与车次 Tab 共用 live_search_service 入口（同一代码路径，单测+运行态双覆盖）。

### 清理与残留

- 临时账号 7 个（t1_train_*）全部停用（status=0）+ 会话吊销（8 条）+ refresh token 删除；凭据/脚本临时文件已删（d:/tmp/t1_*）。
- `12306-govtest` 残留：保留 disabled 态——DELETE 引用保护要求清空全部工具行（无工具删除 API），不做共享治理库 DB 手术（TASK §4b 允许口径）。probe-rest×3 残留未动（非 T1 范围，顺手项待定）。
- 管理端 Idempotency-Key 为治理写操作硬要求（run_authenticated_mutation），脚本化操作需带该头。

### 提交与运行态收口（2026-11-02 用户确认后）

- **PR #232**：`feat/travel-12306-dynamic-capability` → main（c038cb4 feat + 8471ded docs）；**合并待用户确认**（CI 过后合）。
- **TRAIN_MCP_ENABLED 已翻转 false**（主仓库与 worktree .env 同步，用户确认）：语义收窄为「静态回退通道开关」落地。rebuild 后容器内验证：动态通道照常服务（车次 `channel=mcp_dynamic` 6 趟 D3333 06:43 席别中文化；票价 `channel=mcp_dynamic` D3333 二等座 100.0/一等座 160.0 真实票价）——所有 runtime 缺席状态今后一律如实拒绝，彻底 fail-closed。
- **决策点 1/2/3/4 已定稿**（2026-11-02 用户确认推荐项，TASK §5 已更新）：悬浮窗不落生产、撤销保留真实语义、照片先用 Unsplash、抽屉三项导航。D0 剩余范围 = 差距矩阵到锚点级 + TASK §2 升 v1。
- 验证方式备忘：容器内裸进程无快照，service 级验证前需 `asyncio.run(mcp_store.refresh())`（app 进程有 15s 轮询不受影响）；真实上游车次条目键为 `train_no`（无 `train_code`），取码逻辑用 `train_code or train_no`。

### D0 侦察记录（2026-11-02 完成，分支 docs/travel-proto-d0-matrix）

### v1 头号发现：v0 矩阵的首页/列表页判定已过时

v0（2026-11-01）把 Home Hero/快捷查询内嵌/模板照片卡/PlanCard/两列表页标为「新功能/形态对齐」，但 **#220（travel-proto-alignment）在 v0 侦察之后才合并**，且原型第 13–18 轮本身就是「向真实平台对齐」——两者已双向收敛。v1 逐项实测（原型 242KB 锚点提取 + 真实组件代码核对）结论：

- **已对齐（占多数）**：G2/G4、H1–H7 全部、W1/W4–W14、L1–L3——真实侧锚点全部核实（TravelV2Frame.tsx 四件套、TravelHome.tsx Hero+照片+胶囊、itineraries 红色删除钮+弹窗、TravelTemplateCatalog、TravelV2Map 琥珀标记+图例、HistoryPanel 恢复语义 :106）。
- **真差距（剩余新建量）**：
  1. **F1 生成流迁工作台**（P4，最重，L）——chat 页生成 → 工作台内生成+对话伴随（决策 5 含 chat 废弃重定向）；
  2. **W2 概览统计块**（P3，M）——现仅单行 brief，原型六行统计（天气/花费取数口径需设计）；
  3. **W3 天数条形态**（P3，S-M）——现为横向滚动日期卡，原型 segment 药丸+行内「＋增加一天」；
  4. **G3「我的」按钮接抽屉**（P1，S）——现跳 /settings，原型/决策 4=开抽屉；
  5. **G1 令牌微调**（P1，S）——色彩已同源 #2878f5 体系，补 Tailwind theme 高频值+工作台视觉校对。
- **新增排除项**：W14 三源 CDN 回退（原型单文件演示设施；真实 npm leaflet 打包无此需求）。
- **风险提示**：G4 头像抽屉=共享布局组件 MobileAssistantDrawer（layout/，其他域在用），P1 改动前必须查消费面；travelTheme.ts 青绿体系属行程概念页内部，与原型的蓝体系不冲突，不要误改。

### 设计令牌基础（P1 输入）

原型 CSS 实测：无 CSS 变量（扁平 tp-* class，~1245 规则块）；高频值 = 主蓝 #2878f5、深墨 #263b55/#344b66、软底 #f4f8fd、描边 #e4ebf3、圆角 12px/胶囊 9999/8px、字号 11px 基准（38/32px 标题档）。真实 Frame/Home 已用同色系——P1 做增量收口，不建第二套主题。

### 点测基建备忘

原型锚点：data-test 19 个（opt/book/tz/convbad/ics/dayops/gen/edit/leg/train/food/hotel/detail/reorder/add/rain/tzoff/undo/editcard）+ 页面容器 id（tp-pg-home/ws/its/tpl）+ 关键函数（renderOv/dayChipHTML/checkpointTrip/setupMap/downloadRevisionICS 等）。坑清单 HANDOFF §2/§7 必读（可见性置真、锁 tab.id、轮询断言、`:where(#travel-platform-proto) button` 特异性）。

## P1 执行记录（2026-11-02，分支 feat/travel-proto-p1-tokens-nav）

### 改动（v1 范围：G3 我的接抽屉 + G4 旅游导航插槽；G1 令牌按 D0 结论走增量收口）

- `components/layout/MobileAssistantDrawer.tsx`（共享组件，向后兼容扩展）：
  - 导出 `ASSISTANT_DRAWER_OPEN_EVENT` 全局开门信号（复用文件内 AVATAR_EVENT 的 CustomEvent 惯例），外部（底导「我的」）可开抽屉；
  - 新增可选 `navContent(close, interactive)` 插槽：缺省导航（返回门户/记忆）不变，chat 页等既有消费面零影响；interactive 跟随抽屉开合做焦点管理（沿用既有 tabIndex 约定）。
- `components/travel/v2/TravelV2Frame.tsx`：
  - TravelTopBar 传 `navContent=travelDrawerNav`：旅游首页/旅行灵感/我的行程 + 账号设置→/settings（决策 4 定稿口径）；
  - TravelBottomNav「我的」从 `/settings` 链接改为派发开门信号的按钮（原型/决策 4 语义），四项布局不变。
- **G1 令牌决策**：不建第二套 Tailwind 令牌表——D0 实测原型高频值与真实 Frame/Home 内联色值已同源（#2878f5 体系），无消费者的死配置违反删除原则；后续 P3/P5 校对产生的新值按既有 `travelTheme.ts` 增量模式收口。

### 验证

- 新增 `TravelV2Frame.test.tsx` 5 用例：BottomNav 四项布局（三链接+我的按钮）、点击派发开门信号、抽屉导航四项断言、关闭后 tabIndex 焦点管理、**默认导航回归**（无 navContent 时保持返回门户/记忆——chat 消费面保护）。
- `vitest run src/components/travel src/api` → **413 passed**（408 基线 + 5 新增，零回归）；`tsc --noEmit` → **16 错 = main 基线**（travel/MobileAssistantDrawer 相关零错误）。
- rebuild 运行态点测（:3100，移动视口 390×844 + 桌面 1280）：登录→/travel→BottomNav「我的」点击→抽屉滑出（截图留痕）→导航四项/用户名/换形象提示与原型抽屉形态一致；桌面 TopBar 头像钮开同一抽屉。验证账号用后即停（status=0+会话吊销），凭据临时文件已删。

## P3 执行记录（2026-11-02，分支 feat/travel-proto-p3-workbench）

### 范围修正（侦察实证）

- **W3「尾部＋增加一天」为 v0/v1 误读**：原型天数条（renderDayChips）只渲染天 chip，「＋添加一天」仅存在于编辑面板（ed-view data-day='add'）——与真实端现状一致。原型与真实的 chip 差异只在**主题显示**（`第 N 天 · 主题`）。按原型事实源收窄：本阶段只补 chip 主题，不发明行内加天。
- **W7/W11 实测已对齐，零改动**：车次候选卡已有 verificationLabel（可能延迟徽标）+ seatSummary/fareSummary/来源/观测时间；通知条「原始对话关联未验证」文案即验证失败变体。

### 改动

- 新增 `travelOverview.ts`：`buildTripOverview(trip)` 六行取数单一事实源（天数主题=days[].title / 安排=Σitems / 预算·节奏=budget+paceLabel / 预估花费=totals.estimated_cost 标注「未核验」/ 天气=契约位如实占位（当前无实时预报源，不伪造——红线 5）/ 住宿=lodgings[0] 或引导语）；`paceLabel` 从 Workspace 收口至此。
- `TravelV2TripWorkspace.tsx`：桌面概览卡替换为六行统计块（dl/dt/dd 语义结构）；桌面+移动两处天数 chip 补主题显示（truncate 防溢出）。

### 验证

- 新增 `travelOverview.test.ts` 8 用例（六行顺序/主题 join/求和/预算与花费 null 分支/契约位/住宿两态/paceLabel 三档）。
- `vitest run src/components/travel src/api` → **421 passed**（413 + 8，零回归）；`tsc --noEmit` → **16 错 = main 基线**。
- rebuild 运行态点测（模板复制行程：湖山人文/龙井茶乡 两天各 4 项）：六行统计实测 `2 天 · 湖山人文 + 龙井茶乡`/`8 个（跨 2 天）`/`¥4,000 · 轻松`/`¥1,280（未核验）`/契约位/引导语全对；chip 显示主题；截图留痕。验证账号（含孤儿 1330）用后即停，临时文件已删。

## P4a 执行记录（2026-11-02，分支 feat/travel-proto-p4a-generate-panel）

### 改动（生成流引擎抽取 + 工作台生成态）

- 新增 `useTravelPlanGeneration.ts`：V2 生成流引擎 hook（从 TravelChatPage 无行程分支逐行抽出）——SSE 消费/过程行/tool.card/澄清选项/草稿两段式/幂等保存（UNKNOWN 同请求重试）/决策令牌。**红线零复制**：写状态机与 SSE 契约只有一份实现。
- `TravelChatPage.tsx` 重构为消费该 hook：恢复流程/会话关联/时区披露等页面态留在页面，行为零变化——**既有 10/10 测试未改一行全过**（抽取忠实性的回归网）。
- 新增 `TravelWorkbenchGenerate.tsx` + 路由 `/travel/itineraries/new`（query: prompt/conversation_id）：三列生成态工作台——左列草稿概览+天数 chip+草稿地图（TravelV2Map 复用）、中列生成面板（过程行+逐日草稿卡+保存/再调整+写状态条）、右列助手对话全程伴随。保存经服务端确认后 `router.replace` 进正式工作台编辑态。
- **后端一行修复**（P4 迁移暴露的错误契约缺口）：`travel_v2/api/trips.py` `_error` 对 `TripWriteConflictError` 的 detail 从 dict 改为 `str(exc)`——全局 HTTPException 处理器会压平 dict detail 成通用「请求参数有误」，用户看不到冲突原因；str detail 被透传为可读冲突消息。契约测试钉住（test_create_schedule_conflict_returns_readable_message）。

### 验证

- 前端：新增 2 用例（空态骨架+initialPrompt 直达、SSE→草稿→保存→replace 断言）；`vitest run src/components/travel src/api` → **423 passed**；`tsc` → **16 错基线**。后端：契约+ai-edit+document 63 passed。
- rebuild 真机点测（4+ 轮真实链路）：prompt 直达生成态 → 真实 SSE 生成（过程行/工具过程）→ 逐日草稿卡+草稿地图+chip → 保存 → **服务端 SCHEDULE_OVERLAP 冲突如实拒绝且消息可读**（修复验证）→ 再调整撤下 → 对话继续 → 重生成 → 苏州轮触发结构化澄清（需要补充信息）。多轮全程会话稳定，两段式守卫（未确认不落库）全程成立。
- 点测环境备忘：IAB 的 rAF 被挂起（visibility=visible 仍不触发），`nextPaint` 依赖 rAF 的写状态两段式在自动化测试里会停在 VALIDATING——垫片 `window.requestAnimationFrame = setTimeout` 后恢复正常；真实浏览器不受影响（chat 页同款 nextPaint 此前已真实浏览器验收）。

### 已知发现（如实，待后续任务）

- **planner 生成排程反复触发服务端 SCHEDULE_OVERLAP**：真实 LLM 多次生成「柳浪闻莺 → 奥体博览城」相邻安排但未预留 27 分钟车程缓冲，服务端硬校验如实拒绝。这是 planner 提示词与写校验器的对齐缺口（生成面不知道校验面的硬约束），非 P4a 前端缺陷；频繁命中会影响生成流可用性，建议 planner 提示词补「相邻地点衔接时间 ≥ 路程耗时」约束（涉及规划链提示词与评测治理，独立任务）。
- 无冲突草稿的「保存→跳工作台」真机路径本轮未走到（每次生成均命中冲突/澄清）；该路径由新增单测的 replace 断言覆盖，P6 总验收复验。

### 下一步

- P4b：首页输入卡/模板卡/胶囊改指 `/travel/itineraries/new`；`/travel/chat` 废弃重定向（决策 5）；TravelChatPage 生成路径删除与测试迁移。

## P4b 执行记录（2026-11-02，分支 feat/travel-proto-p4b-entry-switch）

### 改动（入口切换 + chat 废弃，决策 5 收口）

- `TravelHome.tsx`：Hero 输入卡 `form action` + prompt 胶囊 href → `/travel/itineraries/new`（首页不再有任何 /travel/chat 引用）。
- `TravelTemplateCatalog.tsx`：空态「开始规划」链接 → `/travel/itineraries/new`。
- `app/travel/chat/page.tsx`：重写为服务端重定向路由（redirect()，决策 5 旧链接兼容）；目标映射抽为纯函数 `app/travel/chat/redirect.ts` 的 `travelChatRedirectHref`——**Next.js Page 文件不允许任意导出**（首版把纯函数放 page.tsx 导出，`next build` 报 "not a valid Page export field"，rebuild 失败后抽出独立模块修复）。
- 删除 `TravelChatPage.tsx`（455 行）+ `TravelChatPage.test.tsx`（10 用例）。
- `TravelV2TripWorkspace.tsx`：移动端顶栏「对话」钮 `travelV2ChatHref` → `travelV2WorkspaceHref`（/travel/chat 已废弃，改指工作台自身）。
- `travelV2Conversation.ts`：删 `travelV2ChatHref`（唯一产出 /travel/chat 的工具函数）；其单测断言行随语义退役。
- 注释同步：`useTravelPlanGeneration.ts`（消费方=工作台生成态、回归网=WorkbenchGenerate.test）、`travelLiveRuntime.ts`（草稿渲染方）、`ToolProcessRows.tsx`、`TravelWorkbenchGenerate.tsx`。

### 测试迁移（红线语义不丢）

- 迁入 `TravelWorkbenchGenerate.test.tsx`（断言逐条保留；保存成功入口断言按 WorkbenchGenerate 语义改为 `routerReplace`）：① UNKNOWN 同幂等键同 payload 安全重试（含二次成功后「行程已保存到 V2」）；② 澄清选项渲染+点击以该内容续聊；③ tool.card 渲染+「帮我排进行程」直通聊天管线（stream 2 次）；④ 决策令牌持久化不显示+刷新恢复后 actionPayload 自动提交。
- 随组件退役（语义已有覆盖，非删除覆盖）：SSE→草稿→保存核心（WorkbenchGenerate.test 既有 2 用例同语义）；5 个 trip 恢复用例（TravelV2TripWorkspace.test 已有等价红线覆盖：无关联不读 URL 对话/服务端权威替换/大写 UUID 规范化/关联失败停用对话）。
- 新增 `app/travel/chat/page.test.ts` 6 用例（trip_id→工作台透传 conversation_id/无会话裸路径/空白 trip_id 不进工作台分支/prompt 保留/prompt+conversation_id 同留/裸访问）。

### 验证

- `vitest run src/components/travel src/api` → **417 passed**（423 基线 −10 退役 +4 迁移，零回归）；`vitest run src/app/travel` → **7 passed**（+6 新增）；`tsc --noEmit` → **16 错 = main 基线**，travel 面零错误。
- `devctl.bat rebuild /y`（worktree 代码）→ frontend rebuilt + healthy（backend healthy 不受影响）。
- 真机点测（IAB，:3100，复用浏览器既有登录态，未新建账号）：
  - `/travel/chat?prompt=杭州慢游` → 最终 URL `/travel/itineraries/new?prompt=…`，生成态三列齐全、textarea 预填「杭州慢游」；
  - `/travel/chat?trip_id=<uuid>&conversation_id=travel-conv-x&prompt=忽略` → `/travel/itineraries/<uuid>?conversation_id=travel-conv-x`（prompt 按设计丢弃；假 trip_id 下工作台如实显示「请求的资源不存在」）；
  - 裸 `/travel/chat` → `/travel/itineraries/new`（conversation_id 由生成态 URL 同步 effect 补写）；
  - 首页 DOM 断言：`form[action="/travel/itineraries/new"]`×1、胶囊 `a[href^=/travel/itineraries/new?prompt=]`×3、旧 /travel/chat 引用×0；
  - 点击胶囊「杭州慢游」→ 生成态预填；输入卡填写「周末去苏州，想看园林」提交 → 生成态预填。
- 点测坑（与 P4a 一致）：IAB rAF 挂起使 Playwright click 的 actionability 轮询卡住（元素实际可见）——DOM `el.click()` 或坐标点击可用；React 受控输入用 prototype setter + input 事件。

### 已知边界（如实）

- `TravelLiveWorkspace.tsx`（V1 遗留孤儿组件，全库无消费方）内部两处 `/travel/chat` 引用未改——由重定向兜底可达生成态；建议后续清理任务整体删除该组件（本次不做范围外删除）。
- 移动端工作台顶栏「对话」钮现指工作台自身；移动端布局暂无内嵌对话区（对话组件仅桌面右列渲染），P5/P6 按 W15 断点校对时复核移动端对话入口形态。
- Next 15 对 server redirect() 的文档请求返回 200 + 内嵌 307 指令（客户端路由器执行跳转），非 HTTP 3xx——curl 直连看不到 Location 头，浏览器最终落点已验证正确。
- 「生成→保存→自动跳工作台」全链真机复验仍属 T2 验收范围（planner 排程冲突未解前保存会如实 422）。


1. **原型侧**：`E:\.zcode\v2\visualizations\c052931e…\travel-platform-proto.html` + 同目录 HANDOFF.md（§4 全功能清单/§5 用户已确认架构决策/§7 坑清单）；`data-test` 锚点可驱动场景（opt/book/tz/convbad/ics/dayops 等）。
2. **真实前端基线**：路由 11 个（app/travel/）；组件 45+ 文件（components/travel/ + v2/）；差距矩阵 v0 见 TASK.md §2（已对齐约七成，新建量集中在视觉令牌/Hero+抽屉/概览统计/生成流迁移）。
3. **后端契约（前端消费，不破坏）**：SSE 事件（stage.*/tool.*/tool.card/done）、V2 REST 17+ 端点、写状态机（UNKNOWN 安全重试）、ai-edit 状态机（pending_resolution）、会话关联校验、health.issues。
4. **12306 迁移（T1）关键事实**：
   - 上游响应契约已在 tools/travel/train.py docstring 固化（四种业务失败结构可区分，解析勿凭文档改）；
   - 唯一消费入口 = live_search_service.search_trains / search_train_price（车次/票价两上游工具 query-tickets/query-ticket-price）；消费方：TravelSearchPanel 车次 Tab、域图 auxiliary query_train、planning_agent 票价补全——**通道选择放 service 层，消费方零改动**；
   - 管理端登记参数（govtest 已验证可复用）：base_url=http://mcp-12306:8000/mcp、transport=streamable_http、auth=none、network_scope=private；治理库现有 `12306-govtest`（disabled 残留，DELETE 409 引用保护，T1 顺手处理）；
   - rollinggo 同类实现参照：backend/providers/travel/live/rollinggo_mcp.py（动态 capability 解析 + mcp_dynamic 执行核心 + resolve 函数模式）。
5. **治理运行态现状**（2026-11-01 核查）：连接 rollinggo（connected，searchHotels approved+agent 授权）/12306-govtest、rollinggo-govtest（disabled 残留）/probe-rest×3（enabled+error 残留，治理卫生可顺手清）；/capabilities count=20/routed=17 与库一致。
6. **历史事故避坑**：本仓库多会话并行——git add/commit 双路径限定、并发 pytest 冻结、容器/端口归属先查、admin 门禁与前端改道原子提交；详见 .zcode/skills/agent-repo-pitfalls/SKILL.md。
7. **未完成边界**（如实维持）：API 闭环预订 BLOCKED（上游 MCP 只读形态，跳转型已上线）；机票/notice/event 契约位无数据源；决策点 1/2/3/4 待 D0 问询。

## T2 执行记录（2026-11-02，分支 feat/travel-proto-t2-planner-schedule）

### 改动与范围

- 修复提交 `5c78453`：仅 `backend/travel/services/transit_service.py` 与新增回归 `backend/tests/travel/test_schedule_travel_buffer.py`。根因及精确修复见上方「T2 专项分析」。validator、前端、提示词及 golden 零改动。
- 本轮将旧提示词根因段整体替换为确定性排程器根因，回填执行证据；既有接力文档提交 `9f54d0a`、`e9220f5` 保留，不重写已推送历史。

### 实际验证

- **T0**：`git diff --check` 通过；`tsc --noEmit` 为既有 16 错（bff-proxy.test.ts 14、chat.test.ts 2），旅游面零新增；类型检查整体仍非全绿。
- **T1/T2（上会话证据，本轮不重复）**：新增排程回归 5 passed，先红后绿；既有排程 73 passed；`pytest backend/tests/travel backend/tests/travel_v2 -q`：1569 passed、84 skipped、9 xfailed，零失败（39 分钟）。
- **前端复核（本轮）**：`vitest run src/components/travel src/api src/app/travel`：48 文件、424 passed = 417+7，20.97 秒。
- **重建（本轮）**：PowerShell 在本任务工作树执行 `devctl.bat rebuild /y`，退出 0。用户已确认覆盖原 ci-blindspots 工作树共享运行态。后端、worker、前端健康；容器 working_dir 指向本任务工作树，容器源码含 `arrive_min = cursor + travel_minutes`，`TRAIN_MCP_ENABLED=false`。
- **T3 定向真实链路（本轮，IAB :3100，真实 LLM/Provider，不使用 mock）**：下列三城均在 `/travel/itineraries/new` 输入需求，真实 SSE 生成、点击「保存为正式行程」、自动跳转 `/travel/itineraries/{trip_id}`，正式工作台加载成功。后端日志恰有三次 `POST /travel/v2/trips → 201 Created`，无 422；PG 127.0.0.1:5433 核对三条正式行程 revision=1/status=active。

| 城市 / 出行日期 | trip_id | 排程衔接实测 |
|---|---|---|
| 杭州 / 2026-11-16（2天） | 6261c828-35bc-54cd-895b-ceb0a3b6c316 | 湘湖 08:30+120min → 45min 车程 → 太子湾 11:15 |
| 苏州 / 2026-11-18（2天） | 48e5d957-9361-51df-9749-0d185216633f | 苏博 09:00+120min → 拙政园 11:05；第二日甪直 08:30+120min → 狮子林 11:58 |
| 福州 / 2026-11-20（2天） | 9035f689-cc37-57ce-9017-790e8a3c0670 | 三坊七巷 08:30+120min → 森林公园 11:03 |

- **清理**：临时账号 id=1333 已 status=0；1 条会话吊销，3 条 refresh token 删除；认证 Redis 中该账号的会话闸键/索引定向清除。核对 active_sessions=0、refresh_tokens=0；凭据未写入文件或文档，登录未开启记住密码。
- **未计入验收的尝试**：重建前端结束前提前发起一轮杭州请求，被容器切换中断（network error），未保存；重建完成后新页面重新进行以上三轮，全部通过。本轮 IAB 保存与跳转正常，无需 rAF 垫片。Playwright MCP 缺其指定 Chrome，直接复用内置浏览器，无安装。

### 已知边界与后续

- **T2 本地状态 PASS**：排程缓冲修复及三城「生成→保存→跳转」完成；PR/CI/合并结果以交付简报为准。未做全仓回归，不把局部通过等同 P6 总验收完成。
- 范围外实测发现：苏州（11/18）与福州（11/20）的助手文案中酒店 deeplink 日期均为 11/16–11/18，与行程日期不一致；「无儿童」仍被解析为亲子偏好。根因未定位，未修改，建议后续针对日期透传及否定槽位单独排查。
- 目的地时区仍显示 UTC 未确认披露；苏州/福州 12306 失败如实降级，远期天气未检查如实披露。天气/机票契约位、API 闭环预订 BLOCKED 等原边界沿用。
- 下一阶段仍为 P2/P5 校对，再做 P6 总验收，本轮不扩大范围。