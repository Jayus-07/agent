# TASK: 旅游 V2 目标投产（travel-v2-production）

> 创建：2026-11-01 · 方向修订：2026-11-01（用户指令：**原型是功能事实源，现有平台按原型补功能**）· 状态：阶段一完成 · Worktree：`D:\Program Files\workplace\agent-wt-travel-seed` · 分支：`feat/travel-v2-template-seed` · 基线：main@0d848a6

## 0. 方向原则（用户已定，勿再偏离）

**原型 = 完善功能的验收基准。** 投产计划以「原型功能清单 → 现有平台逐项对账」为唯一组织方式：
原型有而平台没有/语义不全的，**改后端和前端补齐**；绝不以"后端已有什么"为边界反向裁剪原型功能。

## 1. 原型功能对账矩阵（2026-11-01 侦察定稿）

| # | 原型功能 | 后端现状 | 前端现状 | 判定 → 改造项 |
|---|---|---|---|---|
| F01 | 三轮槽位澄清（结构化选项按钮） | ✅ `clarification_options` 已产出（`backend/travel/models/graph_result.py:80`、`clarification_service.py:158-188`） | ⚠️ V2 页只贴文本（`TravelChatPage.tsx:220-223`），结构化选项只有 V1 Drawer 消费（`TravelChatDrawer.tsx:347`） | **前端**：V2 chat 消费 `clarification_options` 渲染可点选项 |
| F02 | 8 阶段流水线过程行（气泡内折叠） | ✅ stage.started/finished + tool.* 已发 | ⚠️ V2 只渲染阶段芯片；气泡内工具过程折叠（ToolProcessRows）只在 V1 | **前端**：过程行入对话气泡 + 完成折叠 |
| F03 | 逐日草稿卡预览 → 确认保存（两段式） | ✅ done.itinerary 结构够用；createTravelTripV2 幂等已备 | ❌ V2 设计为自动保存（`travelLiveRuntime.ts:38-41` 注释明示绕过预览） | **前端**（方向修订核心）：done 后先渲染逐日草稿预览卡 +「保存为正式行程」，用户确认才落 v1 |
| F04 | 自动优选（餐饮/住宿/交通/天气按行程上下文择优→确认→批量选入） | ❌ 无此能力（最近邻 route.optimizer 与 auxiliary_tasks 只出预览无选入动作） | ❌ 无入口 | **后端+前端**：新增 auto_optimize 编排（聚合查询→择优建议→确认后批量 selection）；前端 A6 入口与确认卡 |
| F05 | 卡片内联 tool 备选（详情卡内备选+二次确认替换/选入） | ✅ search 四件套 + replace_place op 已有 | ❌ 工作台卡片无内联备选 UI | **前端**：卡片详情接 search 结果展示备选，选用走既有 replace_place/selections（不做第二套提交） |
| F06 | 查询三 Tab（food/hotel/train→选入） | ✅ 全链路真实 API | ✅ TravelSearchPanel 已接 | ✅ 已对齐 |
| F07 | 预订确认门（TTL/金额互检/幂等键） | ✅ 机制完整（booking/service.py TTL+指纹+金额互检+CAS+对账） | ⚠️ 仅 fake provider（真实供应商 fail-closed）；V2 工作台无预订入口（只在 chat 域图意图触发） | ⚠️ 前端+接线：V2 行程住宿卡→发起确认门；供应商接 rollinggo 见 F14 |
| F08 | 版本历史/恢复/版本级 ICS/分享 | ✅ | ✅ | ✅ 已对齐 |
| F09 | 写状态三态/幂等重试/CAS | ✅ | ✅ | ✅ 已对齐 |
| F10 | 模板目录/复制 | ✅ API+copy | ✅ | ✅ + 种子（阶段一 B 已完成✓） |
| F11 | 天气-行程联动（日期交集/降水分级预案/必去不换） | ✅ 完整（experts/weather.py、weather_service.py） | ⚠️ 预案展示可在对话卡强化 | ✅ 后端已对齐原型语义 |
| F12 | 地图（Leaflet+高德+估算坐标琥珀标记） | 坐标质量取决于 POI 源 | ✅ TravelV2Map | ✅；坐标质量见 F13 |
| F13 | POI 实时数据 | ⚠️ poi.search 仍 seed 单源（`live/router.py:26-31`；无 AmapPOIProvider） | - | **后端**：实现 AmapPOIProvider + 链账 `("amap_live","seed")`；凭据走 env |
| F14 | 酒店 rollinggo 直连 | ⚠️ 预订仅 fake provider | - | **BLOCKED**：库存权限待上游开通（york.lu@dida.com） |
| F15 | 会话关联/通知条三变体 | ✅ | ✅（URL 不一致变体已接） | ✅ |
| F16 | 动态增删天/60 上限/最后一天不可删 | ✅ | ✅ | ✅ |
| F17 | 目标态布局（助手右列/天数条在地图上/概览卡） | - | ❌ 真实工作台为助手左列、无概览卡 | **前端**：按原型布局改造工作台三列 |
| F18 | SSE card 结构化事件（结果卡内嵌对话） | ❌ tool.* 有字段级结构但无独立 card 事件 | ❌ 无卡片渲染 | **后端+前端**（HANDOFF 实施层 P0 #3）：card 事件 emitter + V2 对话渲染，动作直通既有提交 |
| F19 | 时区未确认警告条（生成行程自动挂） | ✅ health.issues 机制 | ✅ 消费 | 核对项：itineraryToTripDocumentV2 日期待定时是否产生 timezone issue |

## 2. 阶段计划（以矩阵为准重排）

### 阶段一（已完成，2026-11-01）
- A. 原型 9 项修复（验收基准可信化）——详见 PROGRESS。
- B. 模板种子迁移 125（F10 落库）——已完成并验证。

### 阶段二「生成体验对齐原型」（P0，前端为主+后端零改/微改）
- C1（F03）：TravelChatPage 两段式——SSE done 后渲染逐日草稿预览 + 保存按钮，替代自动保存；同步改 travelLiveRuntime 注释语义与 vitest。
- C2（F01）：clarification_options 结构化选项渲染（V2）。
- C3（F02）：气泡内工具过程行折叠（V2 复用 ToolProcessRows 语义）。

### 阶段三「平台新能力」（P0，后端+前端）
- D1（F18）：SSE card 事件 emitter + V2 渲染（动作直通既有提交）。
- D2（F04）：auto_optimize 编排端点/工具 + 前端入口与确认卡 + 批量选入。
- D3（F05）：卡片内联备选（前端组合现有 search + replace_place）。

### 阶段四「接线与数据源」
- E1（F07）：V2 工作台预订确认门接线。
- E2（F13）：AmapPOIProvider + 链账（env 凭据）。
- E3（F17）：工作台目标态布局改造。
- E4（F14）：rollinggo——**BLOCKED**（外部权限）。
- E5（连带项，来自 capability-governance G5 交接）：本阶段若落意图级打点
  （travel intent 进入可聚合数据面），连带翻正域清单目录统计——
  `backend/app/api/routes/travel_inventory.py` 加聚合 SQL（照抄
  `cs_inventory.py` 的 _INTENT_STATS_SQL 模式）+ `stats_available` 翻 True +
  更新 stats_note；测试把降级断言改数值比对（照
  `test_cs_capabilities_inventory.py` 构造比对模式）；协议形状不变，
  聚合不到的维度保持 null（协议语义=该维度无数据）。当前该端点为
  静态清单 + 诚实降级标注（capability-governance PR #204）。

## 3. 提交纪律

- 未提交：等待用户确认后 `git add -A -- <paths>` + `git commit -- <paths>` 双路径限定提交；合并走 PR。
- 提醒：rollinggo/和风 key 在用户桌面 `tool mcp.txt` 明文存放，用后删除、勿入库。
