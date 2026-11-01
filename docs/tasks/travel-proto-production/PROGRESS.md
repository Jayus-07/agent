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

# 总计划全景（2026-11-01 用户决策选项 (b) 后定稿——执行会话以此为准）

## 一、总计划与进度

| 阶段 | 内容 | 状态 | 产出/PR |
|---|---|---|---|
| 前置任务 travel-v2-production | P1–P5：rollinggo live 接线、F18 card、F04/F02/F05、F17 布局+原型对齐、跳转型预订、验收 7 场景 | ✅ 全部合并 | #205/#208/#209/#210/#212/#213/#214/#217/#219/#220 |
| 计划定稿 | 原型生产化 TASK/PROGRESS + 差距矩阵 v0 + 决策点 | ✅ 合并 | #222/#224 |
| **T1 12306 迁管理端能力中心**（用户已决策 b） | 正式连接 `12306` 登记（private 内网）→ discover → approve query-tickets/query-ticket-price → agent 授权（黄金证据）；live_search_service 车次/票价查询加动态通道（动态在场优先→静态直连回退→disabled 如实）；三态/撤销 fail-closed/契约回放测试；govtest 残留清理；rag_smoke 登记 | ⬜ 待开工（方案见 TASK.md §4b，可插队独立执行） | PR + 运行态验收 |
| **D0 侦察定稿** | 差距矩阵到锚点级；决策点 1/2/3/4 问询定稿；TASK §2 升 v1 | ⬜ 待开工 | 矩阵 v1 落 PROGRESS |
| P1 设计令牌与全局骨架 | Tailwind 令牌 + TopBar 胶囊/头像钮 + BottomNav 四项 + 头像抽屉 | ⬜ | PR + 原型点测 |
| P2 首页复刻 | Hero/快捷查询/模板照片卡/最近行程照片卡/空态 | ⬜ | PR + H1-H7 全勾 |
| P3 工作台功能块 | 概览卡统计块/天数条 segment+行内加天/候选卡形态 | ⬜ | PR + W1-W7 全勾 |
| P4 生成流进工作台（最重） | 首页输入→工作台生成态（中列生成面板+右列对话伴随）；chat 页废弃重定向（决策 5 已定） | ⬜ | 2-3 个 PR + 全流程点测 |
| P5 历史/列表/地图形态 | 历史时间线/两列表页/地图多源回退 | ⬜ | PR + L/W9/W14 全勾 |
| P6 总验收 | 原型全页面点测 + 移动端 + 408/tsc + rebuild 全链 | ⬜ | 用户逐页验收 |

**建议执行顺序**：T1（后端独立、随时插队）→ D0 → P1 → P2 → P3 → P4 → P5 → P6；D0 期间一并问掉决策点 1/2/3/4。

## 二、工作树与环境

- **Worktree**：`D:\Program Files\workplace\agent-wt-travel-proto`（分支从最新 main 建，每阶段独立分支+PR）；`frontend/node_modules` = Junction 指主仓库（已建，vitest 直接可跑）。
- **共享栈**：devctl.bat rebuild /y（PowerShell 调用，Git Bash 里 cmd /c 会被路径转换吞掉）；全容器健康基线含 mcp-12306（healthy）与 rollinggo 连接（connected）。
- **PG**：一律显式 `PGHOST=127.0.0.1`（端口 5433，.env 的 localhost 先解析 IPv6 空耗——根因见 travel-v2-production PROGRESS §1）。
- **本机已开的 live 开关**（勿回退）：`TRAVEL_COMMERCE_PROVIDER_MODE=live` + `TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS=rollinggo.cn`（主仓库与 worktree .env 均已配）+ `TRAIN_MCP_ENABLED=true`（T1 后语义收窄为静态回退通道开关）。
- **凭据纪律**：rollinggo/12306/AMAP 凭据只走管理端加密入库或 env，不入库不入文档不入日志；临时 admin 账号用完必清理（注册→提权→用→停用+logout，流程见 travel-v2-production PROGRESS §4）。

## 三、测试纪律（各阶段底线）

- 前端：`vitest run src/components/travel src/api`（当前基线 **408 passed**，每阶段零删断言、只增不劣化）+ `tsc --noEmit`（**16 错 = main 既有基线**，非 travel 面）。
- 后端（仅 T1 触及）：三态/fail-closed/契约回放单测 + 真 PG 门控（`TRAVEL_V2_TEST_POSTGRES=1 PGHOST=127.0.0.1`）集成 + rag_smoke 定向清单登记（AGENTS.md 第 8 条连带）。
- 不跑全量 pytest；跑测前查并发 pytest（`Get-CimInstance Win32_Process` 过滤）。
- 原型对照点测：8124 静态服务 + IAB，HANDOFF §2 坑清单必读（可见性置真、锁 tab.id、轮询断言非固定 sleep、data-test 锚点驱动）。

## 四、验收总清单（P6 收口）

1. 原型逐页面浏览器点测全 PASS（首页/工作台/生成流/模板/我的行程/编辑面板/历史/确认门/优选/查询四 Tab）；
2. 差距矩阵 v1 全项勾选回填 PROGRESS（排除项显式标注）；
3. 前端基线全绿 + tsc 零新增；后端 T1 测试全绿 + rag_smoke CI 过；
4. `devctl.bat rebuild` 后运行态全链：登录→首页→生成→保存→编辑→查询→优选→预订跳转/确认门→历史/ICS/分享，管理端治理视图（rollinggo+12306 两连接）与运行态一致；
5. 移动端断点核对；已知边界如实标注（API 闭环预订 BLOCKED/机票契约位/notice-event 契约位）。

## 五、已知信息（执行会话开工前通读）

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
