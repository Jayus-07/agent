# TASK: 工作台三处 UX 缺陷修复（travel-workbench-ux）

> 创建：2026-11-04 · 状态：**F1/F2 完成并真机验证，PR 提交中**
> 用户反馈（2026-11-04，成都 2 天行程工作台真机）：
> 1. 「选择交通方式的时候 地图还在显示」——「选择路段交通」弹层打开时，行程地图叠在弹层上方（z-index 层级错误，地图盖住弹层左侧）。
> 2. 「鼠标移到行程卡上景点介绍没有」——时间轴行程卡 hover 无景点介绍浮现。
> 3. 「点击午餐 晚餐 这些没有显示可候选的餐厅」——点击时间轴中的餐食项，未出现候选餐厅面板。

## 0. 范围与口径

- 仅改 V2 工作台前端交互（`frontend/src/components/travel/v2/`）；服务端契约/状态机零改动（除非候选餐厅本就有 API 而前端没接——以代码为准）。
- 修复以「既有设计意图被破坏/未接线」为优先假设，不顺手重构。
- 验收：三处真机复现→修复→复测；vitest travel 面 + tsc 基线零新增；rebuild healthy；截图回填 PROGRESS。

## 1. 侦察记录

1. **地图盖弹层（根因确认）**：工作台弹层是 `TravelV2EditDialog`（`fixed inset-0 z-[60]`）与 `TripEditPanels`（z-50）；地图组件 `TravelV2Map`（Leaflet+高德瓦片）外层容器无 stacking context，Leaflet 内部 pane z-index 200-1000 直接参与根层级——z-60 弹层必被盖住。修复 = 地图根容器加 `isolate`（isolation: isolate 收拢 Leaflet 层级），一行根因修复，全部弹层受益。
2. **hover 无景点介绍**：V1 ItineraryView 有 M2.5 降噪模式（`hidden group-hover:block` 渐现辅助信息，ItineraryView.tsx:425），V2 工作台时间轴卡没有移植。数据侧 V2 契约无「介绍」正文（amap 不提供描述文本），诚实上限 = 事实摘要（评分/门票/人均/开放状态/坐标核验）+ note。修复 = 移植 group-hover 渐现行，内容用真实 facts，不编造文案。
3. **餐食点击无候选**：现状餐食卡与普通卡同样只是选中；「备选地点」按钮按 title 关键词搜腾讯 POI（「午餐」必然 no_results），且服务端 `replace_place` 强校验 source=tencent:lbs（amap 商户进不了替换契约）。既有可复用链路 = 美食通道 `searchTravelFoodV2`（amap 商户，有评分/人均/营业状态）+ `addTravelMealV2`（POST /selections/meals，day_id+meal_period+merchant 快照）。修复 = 桌面端点击餐食卡 → 左列详情区出现「候选餐厅」面板（复用二次确认 + transitionTripWrite('selection') 写守卫 + isConfirmedTravelWrite 回执校验），加入后提示占位餐项可经编辑菜单移除；place「备选地点」按钮对 meal 项隐藏。移动端保持现状（底部导航有美食 Tab），记录为已知边界。

## 2. 阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| F1 | 三缺陷定位 + 修复 + 单测 | 定向测试绿 |
| F2 | rebuild + 真机复测 + 截图 | 三处复测通过 |
| V1 | travel 面 + tsc + PR | 全绿 + PR |

## 3. 纪律

独立 worktree `agent-wt-workbench-ux` / 分支 `codex/travel-workbench-ux`（建于 1620a74）；双重路径限定提交；不动共享栈其他容器；重建前确认 base 含全部已合并 main。
