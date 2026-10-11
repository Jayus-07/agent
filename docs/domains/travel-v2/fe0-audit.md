# Travel V2 前端 FE-0 静态审计

## 审计基线

- 产品基线：`docs/domains/travel-v2/product-design.md` v1.0-RC1，允许 FE-0 审计；本轮不进入 FE-1。
- 代码基线：`origin/main` 的 `d542434660f1137a6678690d9d5d3123b0d8c893`（`docs(travel): add V2 design RC1 and domain index`）。已执行 `git fetch origin main`，并确认 `d542434` 是 `origin/main` 的祖先。
- 隔离工作树：`C:/Users/wh/.codex/worktrees/travel-v2-fe0-audit/agent`。
- 分支：`codex/travel-v2-fe0-audit`；审计开始时工作树干净。
- 方法：只读源码、文档和测试清单；未运行测试、未启动浏览器、未调用真实 Provider。浏览器场景仅列入后续验收计划。
- 范围：`frontend/src/app/travel/`、`frontend/src/components/travel/v2/`、`frontend/src/api/travelV2.ts` 及直接相关的 travel SSE/API、测试与产品约束。

## 状态定义

- **REUSE**：现有实现满足核心行为，可以沿用。
- **FIX**：已有实现可沿用，但存在缺陷或契约表达不严谨。
- **BUILD**：页面或核心交互缺失。
- **BLOCKED**：实现需要先确认后端 Commit/Schema 或真实 Provider；不能以 mock 通过验收。

## 能力逐项核查

| 能力 | 状态 | 当前实现与代码证据 | FE-0 结论 |
|---|---|---|---|
| 首页 | FIX | `frontend/src/components/travel/v2/TravelHome.tsx:10-44,58-84,101-120` 使用 V2 API 拉取模板、行程并提供规划/搜索入口；模板只显示前 6 个（79 行）。所谓“浏览全部”链接指向首个模板详情（78 行），当前没有模板目录路由。 | 保留真实数据、卡片和搜索入口；增加目录页并修正链接。归档在服务端成功后才从列表移除，现有行为可复用。 |
| AI 聊天/规划 | FIX | `TravelChatPage.tsx:6,41-64` 使用现有旅行 SSE 与 `resolveTravelStreamOutcome`；`69-95` 在收到可用规划后再通过 V2 创建/保存行程，成功文案在 REST 写入成功后生成。`100-110` 对所有异常统一呈现请求失败。 | SSE `done` 本身没有被当作提交回执，方向正确；但断线/响应丢失时可能存在“服务端已提交、客户端显示失败/未知”的不确定状态，需依产品 D-13/D-14 对账并保持同一逻辑操作幂等键。当前测试只覆盖成功路径（`TravelChatPage.test.tsx:52-77`）。 |
| Trip 工作台 | FIX | `TravelV2TripWorkspace.tsx:51-71` 加载 Trip 和 Revision；`96-153` 通过结构化编辑 API 更新；桌面三栏入口位于约 `197-210`，手机单列约 `213-214`。桌面助手表单在 `206` 行明确提示“V2 行程对话修改接口尚未接入”。 | 工作台布局及正式 Trip API 可复用。P0：route 的 `tripId` 改变后没有清理旧 `trip`；如果新 Trip 读取失败，`error` 被设置但 `trip` 仍非空，`192` 行空态不会出现，旧行程仍可展示和编辑。需要失败导航复现测试及切换时清理/隔离状态。 |
| 时间轴与行程详情 | REUSE | `TravelV2TripWorkspace.tsx:205-215` 已有天数、日程条目、时段/地点和条目到地图的选择联动；`TravelV2EditDialog.tsx:115-154,172-218` 提供地点、活动、顺序、日程等结构化操作。 | 继续复用现有结构化组件。未知时长不可渲染/归算成“0 分钟”；需要覆盖未知时长与健康状态。 |
| 地图 | FIX | `TravelV2Map.tsx:26-34` 把有坐标的地点全部作为 marker；未按 `verification` 过滤或标记。`72-80` 仅绘制点、联动选择并缩放 bounds，没有道路路线几何；无点时 `81` 固定落到杭州坐标；`88` 将位置笼统描述为现有地点或实时查询。 | 复用 Leaflet/高德底图和选择联动；未核验坐标须显式标注或不用于确定性路线，空地图应避免暗示目的地为杭州。道路规划/导航需要后端路线事实，当前没有。 |
| 手动行程编辑 | REUSE | `TravelV2EditDialog.tsx:95-127,130-186,203-218` 调用父级正式编辑流程，`TravelV2TripWorkspace.tsx:96-113` 以当前 Revision 调用 `applyTravelTripEditV2`，收到成功响应后才更新 UI 并显示已保存。冲突处理见 `134-148`。 | 延续服务器校验成功→更新正式 Trip→显示自动保存的契约；不得增加“应用草案”。补充传输失败/响应丢失的 UNKNOWN 状态，不能断言“原行程保持不变”。 |
| AI 编辑工作台 | BUILD | 桌面助手区目前是查询快捷入口；文本提交没有 AI 修改调用，在 `TravelV2TripWorkspace.tsx:206` 明确声明尚未接入。手机端工作台也没有同一 Trip 的助手切换。 | 需建设工作台 AI 编辑交互，但 AI 修改写入 Schema、结构化冲突/`pending_resolution` 生命周期、断线操作状态查证依赖后端对账，未冻结前标记 BLOCKED，不能接 mock 验收。 |
| 自动保存 | FIX | 手动编辑、餐饮/酒店/动车选择成功后使用服务端响应更新 Trip；`TravelSearchPanel.tsx:159-268` 的明确选入动作与查询动作分离。 | 成功保存的路径可复用。`TravelSearchPanel.tsx:188,229,264,290` 对异常统一称“保存失败，原行程保持不变”；超时/断线不能证明未提交，需改为结果未知并提供安全查证/重试策略。`travelV2.ts:278-297` 普通编辑有调用级幂等键，但每次调用默认新生成；选择操作 `330-352` 也自动生成，调用方没有保留同一逻辑操作键的入口。 |
| 撤销/历史 | FIX | `travelV2.ts:267-272,349-352,355-364` 已有 Revision 读取、restore 和 Revision 类型；`TravelV2TripWorkspace.tsx:54-65,155-171` 以父 Revision 执行 restore，成功后产生新 Revision。 | 复用 restore 实现。当前 UI 仅显示最近一次撤销（`200` 行及移动端状态区）；无可浏览历史、Diff、Redo/恢复指定版本入口。产品 D-05 要求 Undo/Redo/历史恢复均通过 Restore 产生新 Revision。历史查询失败时仍允许加载 Trip，但隐藏撤销，可保留并需提示历史不可用。 |
| 模板 | REUSE | `TravelTemplatePreview.tsx:17-40,53-63` 读取单模板详情、显示预览并通过 `copyTravelTemplateV2` 创建新 Trip，查询/预览不写行程。API 在 `travelV2.ts:300-310`。 | 复用模板预览与复制；首页目录入口需 FIX，目录页 BUILD。复制接口及服务端完整校验尚需后端契约/集成确认。 |
| 酒店搜索/选择 | BLOCKED | `TravelSearchPanel.tsx:194-233` 查询候选，明确显示未知价格/房态、选择后标记 `not_booked` 并走 V2 选入 API；API 在 `travelV2.ts:320-321,337-340`。 | UI 状态和写入入口可复用；真实 Provider 来源、授权、数据时效及后端是否返回真实/缓存/禁用状态尚未在 main 确认。不得把种子或 mock 当真实房态/价格通过。 |
| 美食搜索/选择 | BLOCKED | `TravelSearchPanel.tsx:159-192` 查询与明确“加入”分开；选入后才调用餐饮选择 API。搜索和写入适配器分别在 `travelV2.ts:312-318,330-335`。 | 可复用结果卡、来源和时间；真实 Provider/结果有效期与服务端校验待联调。 |
| 动车搜索/选择 | BLOCKED | `TravelSearchPanel.tsx:235-268,279-313` 展示车次、来源和查询时间，明确选择后写 `not_booked`；API `travelV2.ts:324-327,343-347`。 | 查询状态可复用但真实 12306/聚合 Provider 及动态字段真实性待联调。查询进行态在 `TravelSearchPanel.tsx:109,138` 将来源预设为 `12306`/`amap`，实际 source 应只以响应为准；车次卡在 `313` 行有固定“12306 聚合源”标签，与响应 `ticket_source` 可能不一致。 |
| 分享/导出 | BLOCKED | 本基线的 `frontend/src/api/travelV2.ts`、V2 路由及 V2 组件未发现固定 Revision 的分享或导出适配器/页面。旧 `frontend/src/api/travel.ts:565-574` 存在 V1 ICS 导出，不能证明固定 V2 Revision，也不能直接复用冒充 V2。 | 等待分享权限、固定 Revision、撤销/过期、导出格式及后端接口 Schema；不新增/猜测 API。 |
| PC 体验 | FIX | 工作台具备三栏结构，地图与计划栏可复用；AI 区目前不能提交编辑（上文 206 行）。首页/工作台页面具有桌面布局。 | 收口可交互性、状态可见性、键盘/错误路径；Provider 的“实时”文案必须受真实结果状态驱动。 |
| 手机体验 | FIX | 工作台 `213-214` 为单列视图，包含天数、地图、时间轴和服务 Tab；`TravelV2Frame.tsx:40-50` 的底部导航只有首页/行程/我的，未提供设计要求的发现入口。 | 复用单列、底部抽屉和响应式搜索；补发现入口并联通聊天/工作台。浏览器验收应覆盖窄屏、安全区、软键盘、长列表与抽屉滚动。 |

## 跨切面审计

### 正式 Trip、校验和旧“应用草案”

产品 D-03/D-04/D-08/D-16 明确：服务端完整校验及事务成功后自动保存正式 Trip + 追加 Revision；查询只读；V2 不存在“应用草案”。上述 V2 手动编辑/选入的前端路径等待写 API 成功后才显示保存成功；本轮在 V2 正式路径未发现旧版“应用草案”确认机制。`frontend/src/app/travel/itineraries/[id]/page.tsx:4-7` 只在 id=`demo` 时走单独的旧演示 `TravelTripWorkspace`，其 `DemoNotice` 在 `TravelV2Frame.tsx:31-36` 明确标明不保存账号；这不是正式 V2 路径。FE-1+ 必须保持演示路由隔离，不能复活其本地草稿/应用草案行为。

### 结果未知、并发及跨 Trip

- P0 跨 Trip：`TravelV2TripWorkspace.tsx:51-71` 未在开始加载新 `tripId` 时清空旧 Trip；失败时仍可能留下非空旧 Trip。最小复现：工作台先载入 A，客户端路由切到 B 且 B 请求失败，错误设置后 `loading=false`，旧 A 仍通过 `if (!trip)` 检查并被渲染。
- 写入不确定性：手动结构化编辑收到 `409 VERSION_CONFLICT` 会刷新服务端版本；其他网络错误仍用普通错误状态。API 调用默认生成键，不保证用户对同一逻辑操作重试时继续使用原键。写响应丢失时必须显示 UNKNOWN/核对状态，不可声称已失败或仍是旧版本。
- 聊天提交：聊天先解析 SSE，再通过 V2 REST 保存才显示成功，未将 SSE `done` 单独作为保存成功；网络错误路径没有“提交结果未知/安全核对”专门状态。与 D-13/D-14 的最终一致性仍需联调。
- 查询隔离：查询入口是只读，只有用户明确加入/选择才进入写路径；需增加查询不产生 Revision 的集成断言。现有搜索组件变更 `trip_id` 时的清理需覆盖较慢旧响应晚到的跨 Trip 场景。

### 数据语义与错误状态

- `travelV2PlannerAdapter.ts:94,97,118` 把时区固定为 `Asia/Shanghai`、把所有人数放入 adults 且 children=0、把未知接驳时长归为 0；分别与 D-15、人数语义、D-12 不符，属于 P0 数据修复项。
- `travelV2Map.ts:26-34` 不检查坐标可信状态；`travelV2PlannerAdapter.ts:140-156` 能输出 `estimated/unknown`，地图却仍绘制这些位置。
- `TravelChatPage.tsx:100-110` 与 `TravelSearchPanel.tsx:188,229,264,290` 对结果不确定使用失败语义；应区分明确业务拒绝、版本冲突、传输失败且未知是否提交。
- UI 查询卡上的 source/time/disclosure 是好基础；但 Provider 可用性、有效期、真实性必须来自后端结果，而不能由前端品牌标签证明。

### 现有测试与空缺

当前已存在的针对性测试包括：

- API/幂等与 Revision：`frontend/src/api/travelV2.test.ts`。
- 聊天成功规划自动保存：`frontend/src/components/travel/v2/TravelChatPage.test.tsx`，当前只覆盖成功路径。
- 编辑弹窗：`TravelV2EditDialog.test.tsx`；搜索活动状态：`TravelSearchActivity.test.tsx`。
- 规划结果映射：`travelV2PlannerAdapter.test.ts`；SSE 终态解析：`travelLiveRuntime.test.ts`。
- 首页路由/行为有 `frontend/src/app/travel/page.test.tsx`。

本基线没有 `TravelV2TripWorkspace`、`TravelSearchPanel` 或 `TravelV2Map` 定向测试。FE-1～FE-4 测试增量见 `implementation-plan.md`。本轮未运行以上测试，不把静态检查写成通过。

## 并行工作与待联调阻断

- 已发现未合并的旅行后端/前端工作树：`codex/memory-travel-v2-followup`，HEAD `47731825326a70b5dd353d7d093e4a80349bfce6`，含多个已修改后端服务/模型/API/测试和 V2 前端 API、聊天、工作台文件，并新增 `TravelV2TripAssistant`/`travelV2Assistant`。本审计没有读取或改变该工作树的 diff；本报告严格以已 fetch 的 `origin/main` 快照为事实基线。
- 另有 `codex/memory-travel-frontend`（HEAD `eb4af89…`）与 `codex/memory-travel-integration`（HEAD `431918b…`）工作树。须在 FE-1 分支开始前由任务负责人确认其提交/PR 状态，避免重做或覆盖。
- 真实 Provider、后端 Validator/所有正式写入口、AI `pending_resolution` schema/操作状态查证、分享固定 Revision 与访问控制、PDF/图片/ICS V2 导出均待后端当前分支提交 Commit+Schema 后复核。

## FE-0 结论

存在可直接沿用的 V2 首页、聊天、正式 Trip API、结构化编辑弹窗、时间轴/地图、搜索/选入、模板预览和单步 restore。当前主要 P0 是跨 Trip 旧状态残留、规划映射丢失时区/儿童并把未知耗时记零、以及写操作响应不确定时的错误语义/幂等重试。AI 工作台修改、模板目录、完整历史、分享/导出属于缺失能力；Provider 与 AI 冲突处理、跨断线提交查证等必须等后端契约。FE-1～FE-4 仅作为提案列入施工计划，当前停止等待评审。
