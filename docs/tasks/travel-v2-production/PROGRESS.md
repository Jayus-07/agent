# PROGRESS: travel-v2-production

> 2026-11-01 · Worktree `agent-wt-travel-seed` @ feat/travel-v2-template-seed（基线 main@0d848a6）· 未提交
> 方向修订（同日用户指令）：**原型是功能事实源，现有平台按原型补功能**——TASK.md 已重写为原型功能对账矩阵（F01-F19）驱动的四阶段计划。

## 阶段一（已完成）

### A. 原型修复（仓库外，travel-platform-proto.html，9 项）

| # | 修复 | 改动位置（原型行号以修复前计） | 复验结果 |
|---|---|---|---|
| XSS-1 | prompt 进 summaryHTML 前转义 | buildGen 内 `需求原文："+esc(prompt)` | payload 全文转义显示，`__xss1=NOT_TRIGGERED` |
| XSS-2 | ep-kw 属性值整体转义 | `value='"+esc(st.keyword)+"'`（替代只转义双引号） | 无 img 注入，`__xss2=NOT_TRIGGERED` |
| XSS-3 | ITEMS 透传渲染点统一转义 | expandItemDetail（n/t/a）、ws-dmeta、setWsStatus msg、confirmModal title/body、fb msg、编辑面板标题 safeTitle | 详情面板文本化显示，`__xss3=NOT_TRIGGERED` |
| F1 | remove_day/add_day mutate 末尾补 `rebindItems()` | 编辑面板两分支 | 删第 1 天后前移卡点击详情联动（rebindWorks=true） |
| F2 | setMode formal 清空 `#ws-draftprev` + `epFindCard`/editcard `findCard` 限定 `#ws-daylists` | setMode 头部 + 两个查找函数 | 改时间 08:30→06:15 可见 DOM 真实变化；副本容器恒空 |
| F3 | `epReorder(delta,itemId)` 传入面板目标项 + 边界 fb 提示 | menu 视图 data-mv 绑定 + epReorder 签名 | 面板项"鼓山"下移正确换位；边界不再静默假成功 |
| F5 | applySnap 恢复 ITEMS 改深拷贝 | `ITEMS[k]=JSON.parse(JSON.stringify(...))` | 历史恢复→编辑→undo×2 后 DOM 与详情一致（consistent=true） |
| F6 | setRev 内触发查询 Tab 版本胶囊同步 | setRev 追加 syncQPill 调用 | 生成 v1 后胶囊显示"行程 v1"（修复前恒"v3"） |
| F7 | 澄清选项点击后禁用同组按钮 | startDraft ask() 回调 | 连点 3 次仅 1 条回答，hsub 不再被污染 |
| F8 | undo/UNKNOWN 重试/CONFLICT 刷新/自动优选/历史恢复 五处补 `REVSNAPS.push` | 五处 | undo 后历史面板 7 行（修复前 1 行），撤销产生版本可回 |

语法校验：`node -e "new vm.Script(js)"` 通过（130,100 字节脚本）。
回归覆盖：XSS 三点 + F1/F2/F3/F5/F6/F7/F8 场景全部实测通过（IAB，poll 断言）。

### B. 模板种子迁移（worktree）

新增/修改 4 文件：
1. `backend/sql/migrations/129_travel_v2_template_seed.sql`（新增，52 行/16.8KB）：3 份已发布模板（hangzhou-slow/suzhou-garden/xiamen-sea，与前端 demoOnly slug 一致；content 为合法 TripDocumentV2，含真实参考坐标/legs/整理时点 facts；uuid5 确定性 template_id；ON CONFLICT (slug) DO NOTHING 幂等）。注：提交时原编号 125 与先行合入 main 的 125_mall_wallet 冲突，顺延为 129（2026-11-01）。
2. `scripts/init_db.py`：MIGRATION_TARGETS 登记 `"129_travel_v2_template_seed.sql": "memory"`。
3. `backend/tests/travel_v2/test_migration_registration.py`：新增 `test_travel_v2_template_seed_is_registered_and_runs_after_schema`（129 仅依赖 084 建表，101 属 ORDER_LAST 压尾，故不并入原 sequence 断言而单独守护「已登记+晚于建表」）。
4. `backend/tests/travel_v2/test_template_seed_migration.py`（新增）：content 过 TripDocumentV2 严格校验（extra=forbid）+ slug 集合与前端 demoOnly 一致 + uuid5 确定性 + 门控隔离 PG 幂等测试。

验证证据：
- `pytest backend/tests/travel_v2/test_migration_registration.py backend/tests/travel_v2/test_template_seed_migration.py` → **4 passed, 1 skipped**（skip=TRAVEL_V2_TEST_POSTGRES 门控）。
- 一次性 PG 容器（postgres:16-alpine，127.0.0.1:5599，--rm 已删）：084→086→种子依次执行 OK（当时编号 125）；**重放幂等 OK**；落库 3 行、status=published、days 数与 day_count 一致（杭州 2/苏州 2/厦门 3）。
- 生成链路留痕：content 由 `TripDocumentV2.model_validate` 当场校验后固化（生成脚本 `d:/tmp/gen_travel_seed.py`）；校验器曾真实拦截 2 处"无坐标餐饮端点带估算交通值"违规，已按 `reliability=unavailable` 语义修正。

## 阶段二 C1/C2（已完成，2026-11-01）

### C1（F03 两段式草稿预览保存，TravelChatPage.tsx + travelLiveRuntime.ts）
- plan 结果不再自动落库：SSE done 后渲染「行程草稿预览卡」（目的地/人数/节奏 + 逐日卡含 title/start_time/duration + 「保存为正式行程」「继续调整」），用户确认才 `createTravelTripV2`（幂等键在首次保存时生成；UNKNOWN 走既有「同一请求安全重试」，草稿卡保留、保存钮禁用防另起请求）；「继续调整」撤下草稿并留对话提示。
- 文案与语义同步：`行程已自动保存` → `行程已保存`；travelLiveRuntime 注释改为两段式语义（逻辑零改动）。
- 测试：`TravelChatPage.test.tsx` 用例 1/2 改为两段式断言（submit 后 createTrip 不得被调用→点保存→URL/消息/schema 断言），新增「澄清结果渲染结构化选项」用例。

### C2（F01 结构化澄清选项，同文件）
- `clarification_options`（后端 graph_result 已产出、此前 V2 不消费）现渲染为可点选项 chips，点击即以该内容继续对话；新消息发出时自动撤下旧选项。

### 验证证据
- `vitest run src/components/travel/v2/TravelChatPage.test.tsx` → **9 passed**（含 3 个新/改用例）。
- `vitest run src/components/travel src/api` → **43 文件 407 tests 全部通过**（travelLiveRuntime 改动零连带破坏）。
- `tsc --noEmit`：main=16 错误、worktree=16 错误——**零新增**（16 个为 main 既有基线错误，位于 bff-proxy.test.ts/chat.test.ts，与本任务无关）。
- 依赖复用：worktree `frontend/node_modules` 以 Junction 指向主仓库（只读复用，未改主仓库）。

## 阻塞 / 下一步

- 阶段二（SSE card 通道 + AmapPOIProvider）与阶段三（rollinggo，BLOCKED）见 TASK.md §2。
- 用户确认后提交：`git add -A -- backend/sql/migrations/129_travel_v2_template_seed.sql scripts/init_db.py backend/tests/travel_v2/ .github/scripts/rag_smoke_path_filter.py frontend/src/components/travel/v2/ docs/tasks/travel-v2-production/` + 双路径限定 commit（2026-11-01 用户已确认提交合并；下一会话执行）。
- 提醒： rollinggo/和风 key 在用户桌面 `tool mcp.txt` 明文存放，用后删除、勿入库。
