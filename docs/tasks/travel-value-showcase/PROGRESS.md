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

## 2026-11-04 B1–B5 执行（本会话）

- **B1（f6d9696）**：平价定额表 + `estimate_budget_floor(tier)` + 28 用例定向绿（含 2 人 2 天 ≤¥400 / 3 天 ≤¥600 参数化硬口径、economy 零回归对照、定额守卫断言）。
- **B2（2419d27）**：词表/标签/TIER_PROFILES/compression_order/V2 契约 tier + 前端类型与切换器；134 后端定向 + 294 前端 travel 面绿；tsc 修复 1 条新增后回到 17 错基线。
- **B3（7dca287）**：免费 POI 权重槽位（仅 budget 生效）+ repair/weather_service 费用透传 tier；金标 quality_golden + travel_dataset 21 passed 全绿（数据集零档位用例，无需 DATA-01 同步）。
- **B4（c19d8bc）**：TravelShowcase 橱窗 + 筛选 + 空态 + 透传；修复 #299 遗留过期断言（page.test 仍断言已下线种子模板区块）；travel 面 300 绿。
- **B5 真实生成（7630566 nextPaint 修复后）**：
  - 新注册测试账号（owner 1361）真机生成 3 条平价行程并入库：成都美食 2 天 **¥308** / 福州人文 2 天 **¥335** / 厦门海滨 2 天 **¥389**——全部 tier=budget、全部 ≤¥400（3 天 ≤¥600 由地板单测固化）；「穷游/越便宜越好」口语全链路触发平价档（含 V2 契约透传）。
  - 压线场景：厦门 3 天预算 ¥550 → 平价地板 **¥590** 真机如实披露「最低硬成本约 590 元，预算 550 元排不出来」+ 预估 677 超支 127 明示；该演示行程已归档，橱窗保持三卡干净。
  - **过程发现并修复真 bug**：浏览器合成器不渲染时 rAF 永不触发，「生成完成即自动保存」永久卡在 VALIDATING（无 POST、无失败反馈）。修复 = nextPaint race 250ms 兜底（7630566），重建后自动保存正常。
- **V1 证据**：截图 [screenshots/](screenshots/)（v1-showcase-empty / v1-showcase-3cards / v1-workbench-passthrough）；档位筛选真机点测（平价=3 卡；经济=如实空态+规划入口）；卡片点击透传工作台（会话绑定完整）；rebuild 后 backend healthy、3100 就绪。
- **测试终态**：前端 travel 面 **301 passed** + tsc 17 错基线零新增；后端 travel 域全量（tests/travel + tests/travel_v2）**1625 passed / 84 skipped / 9 xfailed，exit=0**（0:40:23）。
- **遗留风险**：平价档估算与实测报价有 ±5% 级偏差（厦门定额 190 vs 实测最低 193），由「预估 · 未核验」披露口径覆盖；`budget` 词表中「省钱」从 economy 移入平价档属有意变更（守卫断言固化）。
