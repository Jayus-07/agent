# HANDOFF-T2：planner 排程约束对齐——执行会话接力稿（2026-11-02 额度截止，下一会话以此为准）

> 本文件是 travel-proto-production 任务的 **T2 阶段接力稿**（五节：总计划/工作树/进度/测试/验收）。
> 上一会话已完成 P4b（合并 #254）并把 T2 的根因修正与排程器修复**代码落地但未提交/未验证完**。
> 读法：先读本文件，再读 PROGRESS.md「总计划全景」与「P4b 执行记录」（在 main@a6bea93 中）。

## 一、总计划与进度

| 阶段 | 状态 | 说明 |
|---|---|---|
| P4b 入口切换 + chat 废弃 | ✅ 合并 | PR #254（merge commit `a6bea93`）：首页/模板入口改指 `/travel/itineraries/new`、`/travel/chat` 服务端重定向、删 TravelChatPage、测试迁移 4 用例入 WorkbenchGenerate.test、新增 chat 重定向 6 用例。决策 5 收口完成，旧链接兼容真机核对通过。 |
| **T2 planner 排程约束对齐** | 🔶 **进行中（本接力稿主体）** | 根因已修正 + 修复代码已落地 + 新回归测试 5 用例全绿（红→绿验证过）；**剩余：定向回归 → rebuild 真机验收 → PROGRESS 回填 → 提交 → PR** |
| P2 首页对照校对 / P5 历史/列表/地图形态校对 | ⬜ | 校对类，预期改动很小 |
| P6 总验收 | ⬜ | 依赖 T2 落地后生成链路可用 |

## 二、工作树与环境

- **Worktree**：`D:\Program Files\workplace\agent-wt-travel-proto`，当前分支 **`feat/travel-proto-t2-planner-schedule`**。
- **分支基点**：本分支已 rebase 到 origin/main（含 #254 P4b 与后续 docs 合并）；T2 修复已落库为 commit `5c78453`（fix(travel) 排程衔接缓冲），本接力稿文件为 docs commit。
- **本分支包含的改动**：
  - `backend/travel/services/transit_service.py`（修改：schedule_day 车程衔接缓冲，commit 5c78453）
  - `backend/tests/travel/test_schedule_travel_buffer.py`（新增：5 用例回归，commit 5c78453）
  - `docs/tasks/travel-proto-production/HANDOFF-T2.md`（本文件）
- **共享栈**：`devctl.bat rebuild /y`（PowerShell 调用：`/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Set-Location '<worktree>'; & .\devctl.bat rebuild /y"`——Git Bash 直调 cmd.exe 的 `/c` 会被吞，必须走 PowerShell）；rebuild 后端才生效（T2 是后端改动，**必须 rebuild**）。
- **PG**：显式 `127.0.0.1:5433`（5432 是宿主机另一套 PG）。
- **`TRAIN_MCP_ENABLED=false`** 是 T1 收窄后的正确状态，勿改回 true。
- **Python**：`D:/Python/python.exe -m pytest`（宿主机直跑，backend 测试不需要容器）。
- **网络坑**：github.com:443 间歇不可达（gh 走 api.github.com 正常）——push/fetch 带重试循环。
- 避坑纪律：`.zcode/skills/agent-repo-pitfalls/SKILL.md` 必读（提交双路径限定等）。

## 三、进度（T2 已完成 / 剩余）

### 已完成（上会话，代码在工作树）

1. **根因修正**（推翻接力稿旧方案）：P4a 现象「生成草稿必然 SCHEDULE_OVERLAP」的根因**不是 LLM 提示词**——start_time 全部由确定性排程器 `backend/travel/services/transit_service.py::schedule_day` 产出（LLM 只经 `planning_agent.propose_day_plan` 决定候选分天；optimization_agent 无 LLM）。真实根因 = 排程光标只推进到 `cursor = end`，已产出的 `leg.minutes` 车程未计入 → 下一项 start = 上一项 end → 与 `trip_write_validator` 的 SCHEDULE_OVERLAP（start ≥ prev_end + leg.duration_min）必然冲突。**不需要动任何提示词/评测 golden**。
2. **修复**（transit_service.py schedule_day）：leg 构造块前移到 start 计算之前，`arrive_min = cursor + travel_minutes`，`start = max(arrive_min, open_min)`，`wait = start - arrive_min`（开门等候语义不被车程污染）；`travel_minutes = max(0, int(est.get("minutes") or 0))`。口径与 validator 精确对齐：`leg.minutes>0` → V2 映射 reliability=verified/estimated → validator 计入；`≤0` → unavailable → 不计入（排程也按 0）。`rebuild_days` 是唯一重排实现（首次排程/天气替换/repair 全走 schedule_day），一处修复全链生效。
3. **新回归测试** `backend/tests/travel/test_schedule_travel_buffer.py` 5 用例（含把 schedule_day 产出内联映射为 validator document 直接调 `validate_trip_write` 断言不抛 SCHEDULE_OVERLAP——口径零漂移）：**5/5 绿**（先红后绿验证：修复前 4 红 1 绿，红用例精确复现缺口）。
4. **既有排程测试**：`test_phase2_input_boundaries.py + test_transit_option.py + test_p0_mvp.py` → **73 passed**（无既有断言依赖旧的无缓冲时刻）。

### 剩余（下一会话按序）

1. **定向回归**：`D:/Python/python.exe -m pytest backend/tests/travel backend/tests/travel_v2 -q`（上会话刚启动即被额度截断；重点看 weather/repair/golden 类测试是否有依赖旧时刻的断言——预期无，73 用例已过）。
2. **rebuild + 真机验收**（T2 验收口径，见第五节）。
3. **PROGRESS 回填**：把本文件「进度」内容并入 PROGRESS.md（含 T2 专项分析的根因修正版本——注意 PROGRESS.md 在 main@a6bea93 的版本才是最新，其「T2 专项分析」仍是旧提示词方案，需整体替换为根因修正版，参考本文件第一节与下方 validator 规则）。
4. **提交 + PR**：双路径限定（`git add -A -- <paths>` + `git commit -m "..." -- <paths>`）；fix(travel) 一个 commit + docs 一个 commit；PR → CI 绿后合并并简报（延续「持续实施」授权）。

### 关键事实（写 PROGRESS / PR 描述用）

**validator 精确规则**（`backend/travel_v2/services/trip_write_validator.py:39-77`，只读未动）：
- SCHEDULE_OVERLAP：同天 items 按数组序，相邻两个有 `start_time` 的项，`下一项.start < 上一项.start + 上一项.duration_min + leg.duration_min` 即冲突（leg 需 from/to 匹配且 `reliability ∈ {verified, estimated}` 且 duration_min 非 None，否则按 0）；无 start_time 的项跳过且不更新链条。
- SCHEDULE_OUT_OF_DAY（end > 24:00）、VERIFIED_CLOSED、VERIFIED_OPENING_HOURS 为其余硬门；ROUTE_TIME_UNKNOWN / OPENING_HOURS_UNVERIFIED 为不阻塞 advisory。
- 口径依据：`estimate_leg` 恒返回 minutes（真实路线失败回退本地估算）；前端 `travelV2PlannerAdapter.mapLegs` 把 `minutes>0` → reliability=verified（is_estimate=false）/estimated，`≤0/null` → unavailable。

## 四、测试底线

- 后端定向：`D:/Python/python.exe -m pytest backend/tests/travel/test_schedule_travel_buffer.py backend/tests/travel backend/tests/travel_v2 -q`；**不跑全量 pytest**；跑前查并发会话（Pitfalls §三）。
- 前端基线（本阶段零前端改动，收口时复核）：`cd frontend && ./node_modules/.bin/vitest run src/components/travel src/api` → **417 passed**（#254 后新基线：423 − 10 退役 + 4 迁移）；`vitest run src/app/travel` → 7 passed；`tsc --noEmit` → 16 错 = main 基线。
- IAB 点测三坑（真机验收用）：rAF 被挂起（Playwright click 的 actionability 轮询会卡住——DOM `el.click()` 或先垫片 `window.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 16)`）；React 受控输入用 prototype setter + input 事件或 Playwright fill()；断言一律轮询页面状态非固定 sleep。

## 五、验收（T2 收口口径，来自原接力计划，不变）

1. **不同城市真实生成 ≥3 次，全部通过服务端创建（无 422 SCHEDULE_OVERLAP）**——rebuild 后用临时账号（用完即停用+吊销：UPDATE status=0 + 吊销 sessions + 删 refresh_tokens；凭据不落盘）在 `/travel/itineraries/new` 走真实 SSE 生成（建议：杭州/苏州/福州或不同城市），草稿保存全部成功。
2. **生成态「保存→自动跳工作台」真机走通**（P4a 唯一未真机覆盖段）：保存成功后 router.replace 进 `/travel/itineraries/{trip_id}`，工作台正常加载。
3. validator 只读未动（git diff 确认）；前端零改动。
4. 已知边界如实标注：天气/机票契约位、API 闭环预订 BLOCKED 等沿用 PROGRESS 既有记录。

## 接手提示词

见仓库外会话交接（用户复制）；本文件第三节「剩余」即执行清单。
