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
