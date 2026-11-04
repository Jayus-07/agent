# TASK: 工作台 UX 二批六项（travel-workbench-ux2）

> 创建：2026-11-04 · 状态：**侦察中**
> 用户反馈（2026-11-04，工作台真机）：
> 1. 「目的地时区尚未确认」横幅一直固定显示占地方 → 可关闭，记住关闭。
> 2. 行程卡三点（MoreHorizontal）应是编辑入口：弹窗内可搜索候选并切换（景点/午餐都要）。
> 3. 左列地图以下全部不要：行程概览、地点详情卡、「查看『三坊七巷』的备选地点」按钮、备选面板、「地图位置根据可用数据展示/V2 工作台」页脚。
> 4. 切换景点/午餐/酒店/交通方式后界面要刷新（改动即时可见，不残留旧数据）。
> 5. 生成行程后自动推荐最优交通方式，不要用户逐段手动点。
> 6. 旅游助手回复出现「## 住宿建议」原文——前端缺 Markdown 渲染。

## 0. 口径

- 服务端写状态机/幂等/SSE 契约不破坏；#5 是**确定性推荐合成**（诚实披露 estimated/待真实路线查询），不接入新外部数据源。
- #2 的候选切换复用既有链路：景点 = searchTravelPlacesV2 + replace_place；餐食 = searchTravelFoodV2 + selections/meals + 移除占位（remove_item，二次写顺序提交）。
- #6 优先复用站内已有 Markdown 渲染组件，不新增依赖。
- 每项独立可验证；travel 面 + tsc 基线 + 后端 travel 域定向/金标零回归。

## 1. 侦察记录

1. **时区横幅**：`TravelV2TripWorkspace` 按 health issue（timezone_unconfirmed/assumed）恒渲染。修复 = 可关闭（×）+ localStorage 按行程记忆，同行程不再弹。
2. **三点编辑**：弹窗菜单已有「替换地点」（tencent POI → replace_place）。缺口 = 餐食占位项走同一入口语义错（会把「午餐」换成景点）。修复 = meal 项菜单改「换家餐厅」视图（searchTravelFoodV2 amap 商户列表），workspace 提供 `onSwitchMeal` 顺序双写（selections/meals 加入 + remove_item 移除占位，各自幂等键与写守卫）；place 项搜索关键词预填标题。
3. **左列地图以下**：桌面 aside 依次是 行程概览 / 地点详情卡 / 「查看备选地点」按钮 / 备选面板（上批加的）/ 候选餐厅面板（上批加的）/ 页脚——全部移除，候选入口统一收进弹窗（#2）；连带删除 inlineCandidates/mealCandidates 机制与 buildTripOverview 引用。移动端详情卡保留（手机端唯一详情区，用户未提）。
4. **刷新**：写路径已更新 trip state（地图/时间轴联动）；用户感知的「不刷新」主要来自 #2/#3 的旧候选面板残留与 add_meal 后断腿（#5 根因之一），随 #3/#5 一并消除；F4 真机逐路径复验（换景点/换餐厅/选酒店/切交通）。
5. **交通推荐**：`apply_operation` 尾部已对全部天调 `_refresh_day_legs`（缺腿合成，写死 transit），但 `add_meal`（独立端点）**不刷腿**——插入餐项后旧 A→B 腿失效、新边无腿 → 「待选择」；adapter 只映射 agent 腿，生成后缺边即待选择。修复 = 新建 `travel_v2/leg_modes.py` 共享推荐（haversine：<1km 步行 / 1-5km 公交 / >5km 驾车，缺坐标回落 transit）+ `_refresh_day_legs` 新腿用推荐（已有腿不覆盖，尊重用户显式 set_leg_mode 与 agent 结果）+ adapter 合成缺失连续对 + add_meal 后刷腿。
6. **Markdown**：`chat/MarkdownContent.tsx`（react-markdown+gfm+breaks+highlight）已存在，MessageBubble 在用；`TravelV2TripConversation` 直接渲染 raw text（whitespace-pre-wrap）。修复 = assistant 消息换 MarkdownContent 渲染，用户消息保持纯文本。

## 2. 阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| R1 | 六项根因定位 | 记录本文件 |
| F1 | #1/#2/#3/#4 前端批次 + 测试 | 定向绿 |
| F2 | #5 后端推荐合成 + 测试 + 金标 | travel_v2 + 金标绿 |
| F3 | #6 Markdown 渲染 | 定向绿 |
| V1 | rebuild + 真机逐项复测 + PR | 六项全过 + 截图 |

## 3. 纪律

独立 worktree `agent-wt-workbench-ux2` / 分支 `codex/travel-workbench-ux2`（建于 da13495）；双重路径限定；不动共享栈其他容器。
