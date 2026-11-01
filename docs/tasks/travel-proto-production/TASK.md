# TASK: 旅游平台原型生产化（travel-proto-production）——原型功能全集复刻进真实前端

> 创建：2026-11-01 · 状态：**执行中（T1 已合并 #232；D0 已定稿，下一阶段 P1）**
> 用户需求原话（2026-11-01）：「现在的生产旅游平台不是用我的原型图，需要把原型图完全复刻到生产环境……首页点和 AI 聊聊跳转的原型图页面，相当于原型图出来用户系统外，全替换原来生产的旅游平台」；补充：「原型图不只布局，还有很多功能是原来旅游平台没有的。目前原型图是可以模拟生产，只是差后端真实数据」。

## 0. 需求解读（执行会话开工前与用户复核一遍）

**核心命题**：原型（travel-platform-proto.html）已经是「可交互的完整目标产品模拟」——功能全集、交互形态、视觉体系都在，跑的是模拟数据引擎。本任务 = **把原型的功能与形态完整落进真实前端（React），模拟数据全部替换为真实后端 API 接线**，替换现有生产旅游平台的 UI 层。

**方向原则**（沿用 travel-v2-production 已验证的原则并升级）：
- **原型 = UI 与功能事实源**：页面结构、交互流、视觉形态、功能块，以原型为准；真实平台已有的实现中，凡与原型形态冲突的，按原型改。
- **真实后端 = 数据与安全事实源**：API 契约、写状态机、幂等、鉴权/租户/属主、会话关联校验、健康语义——一律保留真实链路，原型中的模拟机制（TRIPS 注册表、REVSNAPS 本地快照链、演示数据引擎）**不落生产**，由真实 API 替代。
- 两个事实源冲突时（见 §5 决策点）：UI 形态听原型，数据语义听真实后端。

## 1. 事实源与基线

| 项 | 位置 | 说明 |
|---|---|---|
| **原型产物** | `E:\.zcode\v2\visualizations\c052931e527953356973735bb3f513d190a2976fedd23b49c161fab5067252b2\travel-platform-proto.html`（约 242KB，2026-11-01 第十八轮后） | 单文件，内联 CSS/JS；顶部有 `<meta charset="utf-8">`；`data-test` 锚点可驱动场景 |
| **原型交接文档** | 同目录 `HANDOFF.md` | §3 十八轮演化 / §4 全功能清单 / §5 用户已确认架构决策（勿推翻） / §7 坑清单——**必读** |
| **真实前端基线** | `frontend/src/components/travel/`（+v2/）与 `frontend/src/app/travel/` | main@921baf9 之后（含 #217/#219/#220） |
| **真实前端契约** | `frontend/src/api/travel.ts`、`travelV2.ts` | SSE 事件、V2 REST 客户端、类型——不破坏 |
| **测试基线** | `vitest run src/components/travel src/api` → 408 passed；`tsc --noEmit` → 16 错（main 既有基线，位于 bff-proxy.test/chat.test，非 travel 面） | 每阶段零新增为底线 |
| **原型本地服务** | `cd <原型目录> && python -m http.server 8124 --bind 127.0.0.1`（IAB 不支持 file:） | 用完停；坑清单见 HANDOFF §2（tab 节流/锁定 tab.id/轮询断言） |

## 2. 差距矩阵 v1（2026-11-02 D0 锚点级实测定稿；v0 的 2026-11-01 版已废弃）

图例：**新功能**（真实没有，需新建）/ **形态对齐**（功能在、UI/交互按原型改）/ **已对齐**（#187–#220 已落地，仅细节校对）/ **排除**（原型模拟设施，不落生产）。

**v1 头号发现**：v0 将首页/列表页大量标为「新功能/形态对齐」，但 **#220（travel-proto-alignment）在 v0 侦察之后合并**，已把 Home Hero/快捷查询/模板照片卡/PlanCard/两列表页/TopBar-BottomNav/PageHeading 全量落进真实前端——原型第 13–18 轮本就是「向真实平台对齐」，两者已双向收敛。真实差距集中在：**F1 生成流迁工作台（最重）**、**W2 概览统计块**、**W3 天数条形态**、G1 令牌微调与 G3「我的」接抽屉。P2 从「重构」降级为「对照校对」。

### 2.1 全局骨架与视觉体系
| # | 原型锚点 | 真实现状（v1 实测） | 判定 | 剩余动作/规模 |
|---|---|---|---|---|
| G1 | 扁平 tp-* class 体系（1245 规则块，无 CSS 变量）；主蓝 #2878f5(70处)/深墨 #263b55/软底 #f4f8fd/描边 #e4ebf3；圆角 12px(41)/胶囊 9999(32)/8px(28)；字号 11px 基准 | 色彩已同源：Frame/Home 全线 #2878f5 体系；`travelTheme.ts` 青绿体系（#087b73）属行程概念页内部，不冲突；全局按钮重置坑=原型 `:where(#travel-platform-proto) button`（HANDOFF 坑 8） | **形态对齐（降级）** | tailwind theme 扩展补原型高频值 + 工作台内部逐组件视觉校对；**S** |
| G2 | `TravelTopBar` 胶囊导航（桌面显示/移动隐藏）+右侧头像钮 | TravelV2Frame.tsx 同构实现（active 胶囊 #eaf2ff/#236bd5 + MobileAssistantDrawer 入口） | **已对齐** | P1 点测校对；XS |
| G3 | BottomNav 四项（首页/发现/行程/我的=开抽屉，随页高亮） | TravelV2Frame.tsx 四项已有；但「我的」→ `/settings` 页面跳转，原型=开抽屉 | **形态对齐** | P1：「我的」改开抽屉（决策 4）；S |
| G4 | 头像抽屉 `#tp-drawer`（头像大圆换形象+演示用户+导航） | MobileAssistantDrawer（layout/，326 行）=头像换形象+导航+账号设置→`/settings#profile-memory`；符合决策 4「跳平台统一账号页」选项 | **已对齐** | 校对旅游页入口一致；**共享布局组件，改动需查其他域消费面**；XS |
| G5 | 悬浮窗/FAB/FB toast（`#tp-floatwin`/`#tp-fab`） | 无（决策 1 已定：不落生产） | **排除** | — |
| G6 | 演示数据引擎/TRIPS/REVSNAPS/样例注入 | 真实 API | **排除** | — |

### 2.2 首页（/travel，TravelHome.tsx——#220 后已高度对齐）
| # | 原型锚点 | 真实现状（v1 实测） | 判定 | 剩余动作/规模 |
|---|---|---|---|---|
| H1 | Hero `#hm-form`/`#hm-idea`：badge+两行大标题+输入卡「和 AI 蓝底」+目的地不限 | TravelHome.tsx:53-63 全有（badge「把想去的地方…」/标题「下一段旅程…」/textarea+蓝底按钮） | **已对齐** | P2 对照校对文案/间距；XS |
| H2 | Hero 右图 `#hm-heroimg`：照片池+渐变+「此刻的灵感/慢慢走刚刚好」 | TravelHome.tsx:74：TRAVEL_PHOTOS.westLake + 渐变遮罩 + 双胶囊全有（决策 3=维持 Unsplash） | **已对齐** | 校对照片池覆盖度；XS |
| H3 | 快捷查询三按钮 `#hm-q-*`+内嵌面板 `#hmq-*` | TravelHome.tsx:64-71 三按钮白底描边 + 点击 Hero 内展开 TravelSearchPanel | **已对齐** | 校对；XS |
| H4 | prompt 胶囊三枚（杭州慢游/亲子自然/周末美食） | TravelHome.tsx:72 同文案三枚 → `/travel/chat?prompt=`（P4 后指向工作台生成态） | **已对齐** | P4 同步改指向；XS |
| H5 | 精选模板 `#hm-tplbox`：loading→6 照片卡，桌面 3 列/移动横滚 | TravelHome.tsx:78-81 + TravelTemplateCard（layout=carousel） | **已对齐** | 校对照片封面字段；XS |
| H6 | 最近行程 `#hm-plans`：照片 PlanCard+直接归档 | TravelHome.tsx:93-113 照片卡+已保存+日期·vN+N天+直接归档 | **已对齐** | 校对；XS |
| H7 | 空态（Plane 图标+开始规划） | TravelHome.tsx:85 空态已有 | **已对齐** | — |

### 2.3 生成流程（本任务最重的交互流改造）
| # | 原型锚点 | 真实现状（v1 实测） | 判定 | 剩余动作/规模 |
|---|---|---|---|---|
| F1 | 首页输入/模板/胶囊 → 工作台内生成（中列「正在生成新行程」面板+右列助手全程伴随） | app/travel/chat/page.tsx + TravelChatPage.tsx(455行) 独立页生成 → 保存跳工作台；工作台助手只读问答+ai-edit（TravelV2TripConversation 627行） | **新功能（P4 核心）** | 生成逻辑迁工作台（中列生成面板+右列对话伴随）；chat 页废弃重定向（决策 5）；**L（拆 2-3 PR）** |
| F2 | 三轮槽位澄清结构化选项 | TravelChatPage clarifyOptions chips 已有 | **形态对齐** | 随 P4 迁移；S |
| F3 | 8 阶段过程行入气泡单行替换完成折叠 | ToolProcessRows.tsx(132行) 已接 V2 气泡 | **形态对齐** | 随 P4 迁移；S |
| F4 | 逐日草稿卡 `#ws-p-draft`/`#ws-draftsummary`/`#ws-drafttweak` | TravelChatPage DraftPlanPreview 两段式已有 | **形态对齐** | 随 P4 迁移+逐日卡形态对照；M |
| F5 | 保存自动挂时区警告条 | timezoneWarning 消费已有（Workspace:567） | **已对齐** | — |
| F6 | 边分析边生成+自动滚底 | 流式渲染已有 | **形态对齐** | 滚底细节；XS |

### 2.4 工作台（TravelV2TripWorkspace.tsx 648 行）
| # | 原型锚点 | 真实现状（v1 实测） | 判定 | 剩余动作/规模 |
|---|---|---|---|---|
| W1 | 三列布局（助手右/中时间轴/左地图+概览） | 代码实测 aside order-3 助手右、section order-2 中列、aside order-1 地图列（#217） | **已对齐** | — |
| W2 | 概览卡六行统计 `#ws-ov`/`renderOv`：天数主题/安排跨天/预算节奏/预估花费/天气/住宿 | Workspace:576-578 = 标题+vN+单行（出发·日期·天数·人数·预算·节奏） | **新功能** | 统计块新建；天气/花费取数口径需设计（document.totals/health/selections/预报接口）；**M** |
| W3 | 天数 segment 药丸条+尾部「＋增加一天」`dayChipHTML`/`#ws-list-d` | Workspace:576 = 横向滚动日期卡（min-w-[88px]）；增删天在编辑面板已有（60 上限已有） | **形态对齐** | segment 样式+行内加天（调既有 API）；**S-M** |
| W4 | 行程卡：编号钉/时间/停留/「⋯」/交通行 set_leg_mode | Workspace:574 全有（MoreHorizontal/交通行/餐饮徽标） | **已对齐** | P3 校对；XS |
| W5 | 地点详情卡 `#ws-place-detail` | Workspace:577 详情卡已有 | **已对齐** | 校对；XS |
| W6 | 内联 tool 备选 `tp-xdetail`+「查询替代方案」 | Workspace:578+ inlineCandidates 全链已有（F05） | **已对齐** | 校对；XS |
| W7 | Tabs 四查询面板+候选卡（评分/人均/营业/amap:live/有票候补）+选入二次确认 | TravelSearchPanel.tsx(546行) 全有；车次 Tab 经 T1 动态通道 live | **已对齐** | P3 校对候选卡密度/「有票候补」徽标；S |
| W8 | 编辑面板多视图（menu/add/search/activity/time/move/transport） | TravelV2EditDialog.tsx(272行) 同构 | **已对齐** | 校对；XS |
| W9 | 历史时间线抽屉 `#ws-h1`：版本级 ICS+分享+恢复=新增 Revision | TravelTripHistoryPanel(195行，:106 恢复语义明示)+TravelTripRevisionActions(285行, ICS/分享) | **已对齐** | P5 校对抽屉/时间线样式；S |
| W10 | 写状态条三态 | travelV2WriteState 已有 | **已对齐** | — |
| W11 | 会话关联通知条三变体 | URL 不一致+未关联已有；「验证失败」变体措辞核对 | **已对齐** | P3 补措辞核对；XS |
| W12 | 自动优选 A6 | AutoOptimizePanel 已有（F04） | **已对齐** | — |
| W13 | 确认门 B7+跳转型预订 | BookingConfirmGate+跳转预订已有（#219） | **已对齐** | 校对；XS |
| W14 | 地图：Leaflet+高德瓦片+琥珀估算标记+编号联动+invalidateSize | TravelV2Map.tsx(146行)：npm leaflet 打包+高德瓦片+琥珀/灰估算标记+图例(:142)+联动；原型的三源 CDN 回退=单文件演示设施 | **已对齐**（CDN 回退**排除**——真实工程无此需求） | invalidateSize/容器尺寸校正确认；XS |
| W15 | 移动端 ≤640 单列/≥820 两列 | md/lg 断点体系+移动顶栏（Workspace:540） | **形态对齐** | P6 断点点测核对；S |

### 2.5 列表页（#220 后已对齐）
| # | 原型锚点 | 真实现状（v1 实测） | 判定 | 剩余动作/规模 |
|---|---|---|---|---|
| L1 | templates：TopBar(active discover)/返回/PageHeading/加载态/计数/1-2-3 网格/照片卡 | TravelTemplateCatalog.tsx 全要素（:36-44） | **已对齐** | P5 校对照片卡视觉；XS |
| L2 | itineraries：TopBar(active trips)/标题/加载态/空态/照片卡+红色删除钮+确认弹窗+页内 feedback | itineraries/page.tsx:75-79 全要素（bg-rose-500 删除钮/确认弹窗/feedback） | **已对齐** | — |
| L3 | 离开工作台自动存档恢复 | 真实=服务端 revisions 持久化（强于原型本地快照） | **已对齐** | 真实语义保留 |

### 2.6 域外页面（不在本任务范围）
- `/travel/share/[share_id]`（公开分享页）、`templates/[slug]` 预览：保持现有实现，仅按 G1 设计令牌过一遍视觉，不重构。

## 3. 硬性红线（复刻过程绝对保留的真实语义——违反即返工）

1. **服务端写状态机**：travelV2WriteState（VALIDATING/SAVING/SAVED/UNKNOWN 安全重试/CONFLICT 刷新）与每写操作的幂等键——原型的「直接落 Revision」UI 语义必须经真实写路径。
2. **会话关联校验**：fetchTravelTripConversationV2 未验证不加载对话历史（防跨会话串数据）。
3. **ai-edit 状态机**：applyTravelTripAIEditV2 单项修改/pending_resolution 裁决/决策 token——原型的编辑面板动作直通既有 onEdit 链，不建第二套提交。
4. **选入写路径唯一**：selections 单条幂等（含 expected_revision 链式）；replace_place 直通；跳转预订不产生平台写。
5. **三态降级披露**：live 数据源失败如实（不伪造）；fake 数据带「测试数据」披露；契约位（notice/event/机票）如实「暂无数据源」。
6. **SSE 契约**：tool.card/stage.*/tool.*/done 事件消费不变（后端不为本任务改动）。
7. **租户/属主隔离**：所有列表/详情/分享按 identity 过滤。
8. **测试底线**：每阶段 `vitest run src/components/travel src/api` 全绿（408 基线 + 新增用例）、tsc 16 错零新增；组件重构时旧测试语义迁移不删断言。

## 4. 阶段计划（每阶段独立 commit + 独立 PR；worktree `agent-wt-travel-proto`、分支从最新 main 建）

| 阶段 | 内容 | 主要涉及 | 验收 |
|---|---|---|---|
| **D0 侦察定稿** | 把 §2 矩阵逐项核到「原型 DOM/CSS 区块 ↔ 真实组件/函数」粒度，产出执行清单（每项：原型锚点选择器/真实文件/改动类型/预估规模）；§5 决策点逐条找用户拍板；更新本 TASK.md §2 为 v1 | 原型 8124 服务 + IAB；真实组件通读 | 矩阵 v1 落 PROGRESS；决策点全部有结论 |
| **T1 12306 迁管理端能力中心**（用户已决策选项 b；可与 P1 并行，见 §7 方案） | 正式连接登记（12306，private 内网）→ discover → approve 两工具 → agent 授权；`live_search_service` 车次/票价查询改走动态 capability（静态直连保留为回退）；三态+撤销 fail-closed 测试；清理 govtest 残留 | admin_mcp 运行态操作、live_search_service、新动态 provider 模块、rag_smoke 登记 | 治理视图与运行态一致；撤销授权后如实拒绝；运行态车次 Tab/域图 query_train 均走动态通道 |
| **P1 设计令牌与全局骨架** | Tailwind theme 扩展（色彩/圆角/间距/字号/阴影令牌，对照原型 CSS 变量）；TravelTopBar 胶囊导航/头像钮；TravelBottomNav 四项；头像抽屉组件 | tailwind.config、TravelV2Frame、新 Drawer | 令牌落地后现有页面零回归（408+tsc）；导航/抽屉按原型点测 |
| **P2 首页对照校对**（v1 降级：#220 已落全量） | H1-H7 逐屏对照原型点测，只修真差异 | TravelHome.tsx 校对 | 原型首页对照点测（桌面+移动）；H1-H7 全勾 |
| **P3 工作台功能块** | 概览卡统计块（W2）+天数条 segment+行内加天（W3）+详情卡/候选卡形态（W4/W5/W7） | TravelV2TripWorkspace、TravelSearchPanel | 原型工作台对照点测；W1-W7 全勾 |
| **P4 生成流进工作台**（最重，可再拆 2-3 个 PR） | 首页输入/模板/胶囊 → 携 prompt 进工作台生成态：中列生成面板（草稿卡）+右列助手对话伴随（澄清/过程行/tool.card）；保存落 v1 后无缝切编辑态；chat 页废弃重定向（§5 决策点 5 已确认） | TravelChatPage 生成逻辑迁移、TravelV2TripConversation 扩展、新 WorkbenchGeneratePanel | 原型生成全流程对照点测（三轮澄清→生成→保存→编辑）；会话关联/写状态红线不破 |
| **P5 历史/列表/地图形态** | 历史时间线抽屉形态（W9）+templates/itineraries 列表页（L1/L2）+地图细节（W14 多源回退/琥珀标记） | HistoryPanel、两列表页、TravelV2Map | 原型对照点测；L1-L3/W9/W14 全勾 |
| **P6 总验收** | 原型逐场景浏览器点测（全页面）；移动端断点；408+tsc；`devctl.bat rebuild` 运行态全链；PROGRESS 回填 F 清单 | — | 用户逐页验收 |

> P1/P2 先行可独立交付价值（首页焕新不依赖 P4）；P4 是体验重构核心，安排在骨架稳定后；**T1 独立于前端阶段，随时可插队执行**（后端+治理变更，与前端重构无文件交集）。

## 4b. 12306 通道迁移方案（T1 详细设计，用户已决策选项 b）

**目标**：车次/票价查询从「静态 Tool 直连通道」迁移到管理端能力中心动态 capability 通道，与 rollinggo 同构治理（登记→审批→授权→撤销全在管理端可见可操作）。

**已验证前提**（2026-11-01 实证）：
- mcp-12306 为共享栈正式服务（compose 定义，healthy）；上游 drfccv/mcp-server-12306，Streamable HTTP。
- 上会话治理测试已验证 12306 走能力中心全链可通（审批+授权后 test-invoke 真实车次 217 趟；disable 后 409 fail-closed）。
- 上游响应契约已在 tools/travel/train.py docstring 固化（2026-10-02 实测，解析规则勿凭文档改）：成功 `{"success":true,"count":N,"trains":[...]}`；真无直达 `{"success":false,"error":"未找到该线路的余票","count":0,"trains":[]}`；坏站名 `{"success":false,"error":"车站名称无效","suggestions":[...],"hint":...}`；坏日期 `{"success":false,"errors":[...]}`——四种业务失败**结构可区分**。

**通道设计（消费方零改动）**：所有调用方（TravelSearchPanel 车次 Tab、域图 auxiliary query_train、planning_agent 票价补全）唯一入口是 `live_search_service.search_trains / search_train_price`。在这两个函数内做通道选择：
1. **动态优先**：`mcp_store.get_runtime("mcp.t12306__query_tickets")` 在场（连接启用+工具 approved）→ 走动态通道——新增轻量 provider（参照 rollinggo_mcp.py 模式）：经 mcp_registry 快照解析连接，调 `_McpDynamicToolAdapter.invoke`（复用治理执行核心：运行时在场检查/SSRF/写面强一致——本两工具均 read），响应按 train.py 固化契约解析为现役 data 形态；业务失败结构原样保留（「真无直达」≠错误）。
   - slug 口径（2026-11-02 实测确认）：连接名 `12306` 数字开头，slugify 自动加 t 前缀 → capability 实为 `mcp.t12306__query_tickets` / `mcp.t12306__query_ticket_price`（specs.slugify；计划初稿写 `mcp.12306__*` 有误，已修正）。
2. **静态回退**：动态不在场且 `TRAIN_MCP_ENABLED=true` → 现役静态直连通道原样保留。
3. **如实关停**：两者皆不可用 → 「未启用」如实（不伪造）。
- 链账/日志标注实际通道（mcp_dynamic / static_direct），可观测。
- **静态 ToolSpec（travel.train.search/travel_train_price_tool）保留**——它们是平台能力声明与治理身份，换的只是数据源通道；授权产品口径 = agent（同 rollinggo，勿扩 travel 产品键）。

**管理端登记步骤（运行态操作，流程与 rollinggo #205 同）**：
1. POST /admin/mcp/connections：name=`12306`、base_url=`http://mcp-12306:8000/mcp`、transport=streamable_http、auth_type=none、network_scope=**private**（内网服务名；govtest 连接参数即此，可直接复用）。
2. discover → 枚举 query-tickets / query-ticket-price（还有其他工具按实际枚举处理）。
3. approve 两个工具：read / R1 / 无确认（治理参数照 rollinggo searchHotels 的已批准形态；domain 未分类——agent 授权为运行时唯一 travel 场景口径）。
4. POST grants product_key=agent；**授权闭环黄金证据**：授权后 test-invoke（product_key=agent）真实车次 → revoke 后 `product_capability_denied` → regrant 恢复。
5. 清理：`12306-govtest` 残留连接处理——disabled 且有已审批工具引用保护（DELETE 409），保留 disabled 态或待引用解除后删除（治理卫生，随手项）。

**测试与验收**：
- 三态单测：动态在场（注入快照+mock invoke，回放 train.py 四种契约）→ 走动态；动态不在场 + TRAIN_MCP_ENABLED=true → 走静态；两者皆无 → disabled。
- fail-closed：revoke 授权后动态通道如实拒绝（不回退静态直连**绕过治理**——回退仅限「动态从未配置」，撤销≠回退）。
- 集成：真 PG 门控（TRAVEL_V2_TEST_POSTGRES=1）/ rag_smoke 定向清单登记。
- 运行态验收：管理端治理视图（连接 enabled、两工具 approved、agent 授权）与运行态一致；车次 Tab 与域图 query_train 实际走动态通道（链账/日志证据）；撤销→拒绝→恢复全链点测。
- 已知边界：上游无 SLA（非官方聚合），失败如实「查不了」；开关 TRAIN_MCP_ENABLED 语义收窄为「静态回退通道开关」。

## 5. 决策点状态（2026-11-02 用户确认推荐项后全部定稿；✅=已定）

1. ✅ **悬浮窗/FAB/FB toast 不落生产**（2026-11-02 用户确认推荐项）：原型悬浮窗是测试驱动设施，生产操作反馈沿用现有写状态条/页内 feedback。
2. ✅ **撤销/恢复语义保留真实语义**（2026-11-02 用户确认推荐项）：恢复=新增 Revision（版本全保留，服务端账本），UI 按原型的时间线形态呈现。
3. ✅ **Hero/模板/行程照片源先用 Unsplash**（2026-11-02 用户确认推荐项）：按原型原样接外链，后续可换本地素材池/高德静态图。
4. ✅ **「我的」抽屉只放三项导航+用户名展示**（2026-11-02 用户确认推荐项）：「旅游首页/旅行灵感/我的行程」三项；「账号设置」置灰或跳平台统一账号页（若有）；「换形象」彩蛋不落。
5. ✅ **chat 独立页（/travel/chat）不要**（用户 2026-11-01 确认）：生成流全部迁入工作台；`/travel/chat` 改造为重定向进工作台生成态（旧链接兼容），不再保留独立生成页。P4 执行时同步删除 TravelChatPage 生成路径与相关测试迁移。
6. ✅ **车次 Tab：12306 迁管理端能力中心（用户已决策选项 b）**：
   - **事实修正（2026-11-01）**：本机 `TRAIN_MCP_ENABLED=true` 已显式开启，mcp-12306 是共享栈正式服务（healthy），运行态验证福州→厦门真实车次返回正常——**车次 Tab 当前就是可用态**，不存在「默认关」。此前计划里写「维持默认关」是把代码默认值误当运行态，已修正。
   - **「灰度」的真实含义与依据**（tools/travel/train.py docstring）：12306 没有官方开放 API，上游 drfccv/mcp-server-12306 是**非官方聚合、无 SLA、仅供学习研究**——开关默认 False 是「新部署不自动开、由部署者显式决定」的部署策略，不是本机现状；关闭时如实「未启用」、失败时如实「查不了」（不伪造），主链不受影响。
   - **用户决策**：把 12306 正式登记进管理端能力中心、车次/票价查询改走动态 capability——详细方案见 §4b（T1 阶段）；(c) 清理 `12306-govtest` 残留并入 T1 顺手做。

## 6. 纪律与环境（承接仓库 AGENTS.md）

- Worktree：`D:\Program Files\workplace\agent-wt-travel-proto`（分支从最新 main 建）；所有改动走 PR；提交双路径限定。
- 前端依赖：node_modules Junction 指主仓库（已建）；vitest/tsc 命令与基线见 §1。
- 原型点测：8124 服务 + IAB；HANDOFF §2 坑清单必读（可见性、tab.id 锁定、轮询断言）。
- 每阶段 PROGRESS.md 记证据；「原型对照点测」截图/断言留痕。
- 不跑全量 pytest（本任务纯前端+既有 API 消费）；后端零改动为默认（若 P4 迁移需要后端配合，单独评估走独立任务）。
