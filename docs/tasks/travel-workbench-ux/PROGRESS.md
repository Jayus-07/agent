# PROGRESS: travel-workbench-ux

> 任务主文件：[TASK.md](TASK.md) · 状态同步以 TASK.md 头部为准。

## 2026-11-04 F1+F2 完成（本会话）

- **F1（626614d）**：三缺陷修复 + 定向测试（map/workspace 33 绿，tsc 17 错基线）。
- **F2 真机复测（rebuild healthy 后，成都行程工作台）**：
  - 缺陷 1 ✓：打开「选择路段交通」弹层，弹层完整浮于地图上方，无遮挡（[fix1-dialog-above-map.png](fix1-dialog-above-map.png)）。
  - 缺陷 3 ✓：点击「午餐」卡 → 左列出现「候选餐厅」面板（amap 真实商户：悦百味/许家菜/烤匠等，评分/人均/营业状态），两步确认「加入午餐」→ 服务端 v2 落库（DB 核对 revision=2、烤匠入 items）。
  - 缺陷 2 ✓：hover 「成都武侯祠博物馆」卡 → 介绍行渐现「评分 4.8 · 门票免费 · 坐标已核验 · 09:00 才开放…」，全部真实 facts（[fix2-hover-intro-and-fix3-meal-panel.png](fix2-hover-intro-and-fix3-meal-panel.png) 同帧含候选面板）。
- **测试终态**：前端 travel 面 **303 passed**；workspace 29 + map 定向绿；tsc 17 错既有基线零新增（修掉自己引入的 2 个测试类型错后回到基线）。
- **已知边界**：移动端布局无左列详情区，餐食候选暂只接桌面工作台（移动端走底部导航「美食」Tab，既有能力）；TravelLiveMap/TravelRouteMap 未挂 modal，未动（同款 isolate 一行可加，留作后续）。
