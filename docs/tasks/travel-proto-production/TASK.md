# TASK: 旅游平台原型生产化（travel-proto-production）——原型功能全集复刻进真实前端

> 创建：2026-11-01 · 状态：**计划阶段（未开工）** · 执行会话从「D0 侦察定稿」开始
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

## 2. 差距矩阵 v0（2026-11-01 侦察；D0 阶段逐项定稿为执行清单）

图例：**新功能**（真实没有，需新建）/ **形态对齐**（功能在、UI/交互按原型改）/ **已对齐**（#187–#220 已落地，仅细节校对）/ **排除**（原型模拟设施，不落生产）。

### 2.1 全局骨架与视觉体系
| # | 原型 | 真实现状 | 判定 |
|---|---|---|---|
| G1 | 原创视觉体系（自定义 CSS：色彩/圆角/间距/字号/渐变/胶囊） | Tailwind 高密度小字风（text-[9px]/[10px]） | **形态对齐**：建立设计令牌（Tailwind theme 扩展），逐页替换——用户感知「不是我的原型图」的主要来源 |
| G2 | TopBar：logo+胶囊导航（桌面显示/移动隐藏）+右侧头像钮 | TravelV2Frame TravelTopBar 存在 | **形态对齐**（核对胶囊导航与头像钮） |
| G3 | BottomNav 四项（首页/发现/行程/我的，fixed，随页高亮） | TravelBottomNav 存在（首页/chat 有挂载） | **形态对齐**（核对项数/高亮/「我的」指向） |
| G4 | 头像抽屉（头像大圆循环换形象+演示用户+导航四项，fixed 右滑） | 无 | **新功能**（「我的」抽屉；换形象属演示彩蛋，见 §5 决策点 4） |
| G5 | 悬浮窗演示引擎+FAB+FB toast 体系（12 场景按钮、A/B 直达、拖动互斥） | 无 | **排除**（测试驱动设施；操作反馈沿用真实 writeStatus/页内条——见 §5 决策点 1） |
| G6 | 演示数据引擎/TRIPS 注册表/REVSNAPS 本地快照链/样例 JS 注入 | 真实 API | **排除**（由真实 API 替代——本任务的接线本体） |

### 2.2 首页（/travel，TravelHome.tsx）
| # | 原型 | 真实现状 | 判定 |
|---|---|---|---|
| H1 | Hero：badge+两行大标题+描述+输入卡（「和 AI 聊聊」蓝底）+目的地不限胶囊 | 无 Hero（TopBar 后直接快捷查询区） | **新功能** |
| H2 | Hero 右图：Unsplash 真实照片池+渐变遮罩+「此刻的灵感/慢慢走刚刚好」 | 无 | **新功能**（照片源见 §5 决策点 3） |
| H3 | 快捷查询三按钮（白底描边）点击在 Hero 内展开只读搜索面板 | 快捷查询 grid 存在（形态不同） | **形态对齐** |
| H4 | prompt 胶囊三枚（杭州慢游/亲子自然/周末美食）→ 点击进生成 | 无 | **新功能** |
| H5 | 精选模板：接口态 loading→6 张照片卡（照片封面/渐变/N天N晚胶囊/meta 行；桌面 3 列/移动横滚 snap） | TravelTemplateCard carousel 已有（grid/横滚已有） | **形态对齐**（照片封面来自模板数据？核对字段） |
| H6 | 我的最近行程：照片 PlanCard（图+标题+已保存+日期·vN+N天·点击查看+**直接归档无确认**） | PlanCard 已有（归档语义核对：真实=直接归档） | **形态对齐** |
| H7 | 空态（Plane 图标+开始规划） | 已有 | **已对齐** |

### 2.3 生成流程（首页输入 → 工作台）——**本任务最重的交互流改造**
| # | 原型 | 真实现状 | 判定 |
|---|---|---|---|
| F1 | 首页输入卡「和 AI 聊聊」/模板卡/胶囊 → **直接进入工作台**：中列显示「正在生成新行程」面板（草稿·未保存/规划中/保存为正式行程/再调整一下），右列助手对话全程伴随（澄清问答在对话面板内） | 首页输入 → `/travel/chat`（TravelChatPage）独立页生成 → 确认保存 → 跳工作台；工作台助手仅只读问答+单项 ai-edit | **新功能（交互流重构）**：生成流迁入工作台（streamTravelPlanV2 + 澄清 chips + tool.card + 过程行 + 草稿卡 + 保存），chat 页去留见 §5 决策点 5 |
| F2 | 三轮槽位澄清：结构化选项按钮，点即继续，选项点击后禁用同组 | TravelChatPage clarifyOptions chips 已有（C2） | **形态对齐**（迁入工作台对话面板） |
| F3 | 8 阶段流水线过程行入气泡，单行替换，完成折叠可展开 | ToolProcessRows 已接 V2 气泡（F02/P3） | **形态对齐** |
| F4 | 逐日行程卡草稿预览（草稿态标注）→ 确认保存落 v1 | DraftPlanPreview 两段式已有（C1） | **形态对齐**（逐日卡形态对照原型） |
| F5 | 保存后自动挂时区警告条（日期待定=UTC 占位） | timezoneWarning 消费已有（F19） | **已对齐** |
| F6 | 边分析边生成+自动滚底 | 流式渲染+过程行已有 | **形态对齐**（滚底细节） |

### 2.4 工作台（TravelV2TripWorkspace.tsx）
| # | 原型 | 真实现状 | 判定 |
|---|---|---|---|
| W1 | 三列布局：地图+概览左（天数条地图上方、概览卡地图下方）、时间轴中、助手右 | #217 已对齐 | **已对齐** |
| W2 | 行程概览卡六行统计：天数主题 / 安排（跨天）/ 预算·节奏 / 预估花费 / 天气（出行日临近联动预报）/ 住宿（未安排） | 概览卡仅 brief 一行 | **新功能**（统计块；天气/住宿/花费取数：document.totals、health、selections、未来=预报接口） |
| W3 | 天数条 segment（药丸高亮）+ 尾部「＋增加一天」直加 + 动态增删天（60 上限/最后一天不可删） | 天数条按钮行已有（P4 迁地图列）；增删天在编辑面板（60 上限已有） | **形态对齐**（segment 样式+行内加天） |
| W4 | 时间轴行程卡：编号钉、时间/停留、「⋯」编辑菜单（替换/时间/移动/删除）、交通行 set_leg_mode 面板 | 行程卡+MoreHorizontal+交通行已有（形态核对） | **形态对齐** |
| W5 | 地点详情卡（点击钉位/编号联动） | 详情卡已有 | **形态对齐** |
| W6 | 卡片详情内联 tool 备选（tp-xdetail）+「查询替代方案」→ 备选+二次确认替换 | F05 内联备选已有（P3） | **已对齐**（形态校对） |
| W7 | Tabs 查询面板（行程/美食/酒店/车次）+ 候选卡（酒/餐/车次形态：评分/人均/营业实测/amap:live 标注/有票候补）+ 选入二次确认（美食可选目标天+餐段） | TravelSearchPanel 全有（选入带 day/period 选择器；车次 TRAIN_MCP_ENABLED=true 已开、运行态验证真实车次返回） | **形态对齐**（候选卡视觉/「查看」详情形态） |
| W8 | 编辑面板多视图（menu/add/search/activity/time/move/transport） | TravelV2EditDialog 同构视图已有 | **已对齐**（形态校对） |
| W9 | 历史时间线抽屉：每行版本快照+**版本级 ICS 下载（真实 Blob）**+分享链接（创建/复制/撤销）+恢复=新增 Revision | TravelTripHistoryPanel + TravelTripRevisionActions（ICS/分享/恢复）已有 | **已对齐**（形态校对：抽屉/时间线样式） |
| W10 | 写状态条：SAVED/UNKNOWN 重试/CONFLICT 刷新 | travelV2WriteState 三态已有 | **已对齐** |
| W11 | 会话关联通知条三变体（不一致/未关联/验证失败） | URL 不一致+未关联变体已有 | **已对齐**（补「验证失败」变体措辞核对） |
| W12 | 自动优选（A6）：按行程上下文四类查询→报告卡→确认选入 | AutoOptimizePanel 已有（F04） | **已对齐** |
| W13 | 确认门（B7）：住宿报价→TTL→确认→订单号 | BookingConfirmGate（quote→confirm→status）已有 + 跳转型预订（#219） | **已对齐**（形态校对） |
| W14 | 地图：Leaflet+高德瓦片（三源 CDN 回退+8s 超时兜底）、估算坐标琥珀标记、编号联动、invalidateSize 校正 | TravelV2Map：leaflet+高德已有（动态 import） | **形态对齐**（多源回退/琥珀标记/联动细节核对） |
| W15 | 移动端：≤640px 单列堆叠/顶栏换行/悬浮窗全宽；≥820px 工作台两列 | 真实有 md/lg 断点体系 | **形态对齐**（按原型断点核对） |

### 2.5 列表页（/travel/templates、/travel/itineraries）
| # | 原型 | 真实现状 | 判定 |
|---|---|---|---|
| L1 | templates：TopBar(active discover)/返回链接/PageHeading（Sparkles eyebrow+大标题）/加载态/「已加载的精选模板」+计数/1-2-3 列响应式网格+6 张照片卡 | TravelTemplateCatalog 已有 | **形态对齐**（PageHeading/计数/照片卡视觉） |
| L2 | itineraries：TopBar(active trips)/标题块/加载态/空态（MapPin+开始规划）/照片卡+ArrowRight+**红色删除钮**+确认弹窗+页内 feedback | itineraries/page 已有（#201 时代对齐过） | **形态对齐**（红色删除钮/确认弹窗核对） |
| L3 | 离开工作台自动存档现场（checkpointTrip）→ 列表页重开恢复 | 真实=服务端 revisions 持久化（强于原型本地快照） | **已对齐**（真实语义保留） |

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
| **P2 首页复刻** | Hero（输入卡+胶囊+照片右图+渐变）+快捷查询内嵌面板+模板照片卡+最近行程照片卡+空态 | TravelHome.tsx 重构 | 原型首页对照点测（桌面+移动）；H1-H7 全勾 |
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
