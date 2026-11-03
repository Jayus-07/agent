# PROGRESS: travel-value-showcase

> 任务主文件：[TASK.md](TASK.md) · 状态同步以 TASK.md 头部为准。

## 2026-11-04 立项

- **需求来源**（用户原话 2026-11-03/04）：「目前的行程结果经济还是太贵了 压到最低预算可以是多少钱 很多人不太想花钱 除了交通方式的话」；「上一版的首页 有推荐行程 这个要加上 用真实的生成行程结果 加一个按照经济 风格 区分」；「不能直接说穷游 这个词不好听」；「推荐行程区块的功能你要深入一下理解一下是让用户最快了解项目的功能」。
- **侦察结论（本会话已完成）**：
  - 贵的根因 = 定额体系是「城市均值」口径（`config/travel.py:210`：餐 ¥120-160/人/天、住 ¥350-520/晚；未登记城市回落 150/420）——economy 档 2 人 2 天地板 ¥830-1,160。
  - 真实低价供给已在平台出现过：rollinggo 直连酒店 ¥80/106/151/晚（酒店比价卡真机数据）；小吃带人均 ¥34-40（原型/真实 POI 数据）。
  - 按低价带（餐 40 + 住 120）推算：2 人 2 天 ≈ ¥310，3 天 ≈ ¥520——**2 人 2 天 ≤ ¥400 / 3 天 ≤ ¥600** 作为验收数字。
- **已产出**：本 TASK.md（差距矩阵/阶段计划/红线/决策点/验收清单）。
- **阻塞**：PR #299 待用户确认合并（本任务 worktree 从合并后的 main 建）。

## 2026-11-04 D0 侦察定稿（本会话）

- **开工前置已过**：PR #299 已合并（5cc520f，经用户确认）；worktree `agent-wt-travel-value` / 分支 `codex/travel-value-showcase` 建于合并后的 origin/main；frontend node_modules Junction 与 .env 已就位。
- **rollinggo live 低价酒店实测**（`search_hotel_offers`，1 晚/1 间/2 人，5 城 × 最低 3 家）：福州 145/160/182 · 厦门 193/210/213 · 成都 85/99/188 · 西安 87/87/110 · 杭州 80/92/146。证据落盘 [d0-rollinggo-hotel-samples.json](d0-rollinggo-hotel-samples.json)，复跑脚本 [d0-hotel-sample.py](d0-hotel-sample.py)。
- **契约纠偏（实测推翻 TASK 初稿）**：V2 `TripBriefV2`（`backend/travel_v2/models/trip.py:30`）只有 `interests`/`requirements`，**无 `tier`**；V1 `TravelBrief.tier` 存在（brief.py:141）。档位筛选方案：V2 契约加 `tier`（Literal，默认 economy 兼容旧数据）+ adapter 映射 + 前端类型同步；风格筛选直接用 `interests`。
- **D0 定稿**（详见 TASK §5）：口径 (a) 独立低价定额表；餐 ¥35/人/天统一；住 成都/西安/杭州 ¥90、福州 ¥150、厦门 ¥190、默认 ¥130；地板验算全部城市 ≤¥400（2 天）/≤¥600（3 天）。
- **下一步**：B1 平价定额与地板（`config/travel.py` 新增 `TRAVEL_CITY_BUDGET_COST_TIERS` + `estimate_budget_floor(tier)` + 单测固化 400/600）。
