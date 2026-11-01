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
- ~~用户确认后提交~~ → **已于 2026-11-01 提交并经 PR #187 合并 main**（含种子迁移 125→129 改号、C1/C2、四份文档；顺带落库了本地 main 独有的 conftest 快照回收修复 59bcde9）。
- 提醒： rollinggo/和风 key 在用户桌面 `tool mcp.txt` 明文存放，用后删除、勿入库。

---

# 收编重构进度（feat/travel-domain-consolidation，执行会话续写）

> 2026-11-01 开工 · Worktree `agent-wt-travel-consolidation` · 分支 `feat/travel-domain-consolidation` · 基线 origin/main（PR #187 合并后）。
> 执行依据：同目录 DOMAIN-CONSOLIDATION-PLAN.md v2（§7 顺序：D → B → U1-U5）。

## 前置（已完成）

- seed worktree 在途改动处理（用户确认方案 A）：改号 125→129 + init_db/rag_smoke_path_filter 登记 + 定向测试（16 passed+1 skipped；迁移守卫 12 passed）→ 3 笔双路径限定提交 → rebase origin/main（解 init_db/路径过滤两处登记冲突）→ PR #187 全检查通过合并。
- 发现并随 PR 落库：本地 main@0d848a6（conftest 快照回收修复）从未推送远端，rebase 时作为 59bcde9 携带，PR 描述已标注。

## D 系列（commerce 收编删壳）——已完成

1. **收割（§3.1）**：8 个实现文件（request/extract/models/ranking/identity/cities/deeplink/normalize）git rm + Write 重建至 `backend/travel/providers_support/commerce/`（内部 import 与路径头同步）；新增 `offer_search.py`（从原 service.py 抽取确定性重搜编排，去 telemetry 壳依赖）；新增 `backend/travel_v2/services/hotel_search_service.py`（三态封装 + fail-closed + Decimal 字符串契约）。booking 三处 import 改向（graph_builder×2 过渡、service._resolve_offer、revalidate）；`providers/travel/live/fake_commerce.py` config import 改向。
2. **配置**：`config/travel_commerce.py` → `config/travel_offer_search.py`（TRAVEL_COMMERCE_ENABLED 域开关与无消费方的 EMPTY_TTL 退役；TRAVEL_COMMERCE_* 环境变量名保留兼容 .env）。
3. **删壳（§3.2）**：commerce 包余下 10 文件全删（register/graph_builder/graph_node/graph_node_render/graph_state/service/health/reporter/telemetry/__init__）；domains 注册行删除。
4. **联动（§3.3）**：commerce_prefilter 整删 + prefilter_chain 链项移除；continuation/booking_pending_resolver 移除 commerce 续填（booking 保留）；capability_router 别名、llm_context KNOWN_DOMAINS、product_context 禁行根、health 组件、roundtable 人设/工具箱；evaluation 侧 models MODULE_KINDS/PROBE_MODULES/builtin/release_gate/builder/capabilities/cli/domain_contracts + 删 runners/travel_commerce.py、datasets/travel-commerce/、scripts/e2e_travel_commerce.py；frontend-admin 三件（roundtable 配色/AddToEvalModal/domain-attribution.test）；README + evaluation-governance + evaluation-v2-api + ci-evaluation-environment。**计划外发现**：lock_domain.py 引用已删的 is_commerce_request → 改引收编件 detect_commerce_intent（信号面等价）。
5. **测试（§3.4）**：旧 tests/travel/commerce/ 7 文件处置——语义回归迁移至 `backend/tests/travel/providers_support/commerce/`（conftest/contracts/deeplink/service/availability 全量口径 + routing 的意图判定段抽为 test_commerce_extract.py；reporter/routing 图测随壳退役），registry/router/semantic/entry_mode/module_kind/golden_contracts/dataset_governance 断言同步；新增 `tests/travel_v2/test_hotel_search_service.py`（三态/fail-closed/参数绑定）。
   - **对计划的偏离记录**：计划写「删 7 文件」+「hotel_search_service 单测」，执行时按 §6 契约矩阵「normalize 三道门断言（收编件）必须存活」要求迁移保留了实现层语义测试（5 迁 2 删），避免删除仍存活的 fail-closed 回归面。

### D 系列验证证据
- `pytest backend/tests/travel/providers_support/commerce/ backend/tests/travel_v2/test_hotel_search_service.py backend/tests/test_domain_registration.py backend/tests/orchestration/{test_runtime_registry,test_domain_semantic_consistency,context/test_booking_pending_resolver,router/,graph/test_entry_mode_handoff}.py backend/tests/evaluation/test_module_kind_single_source.py backend/tests/evaluation_v2/test_module_golden_contracts.py` → **326 passed**（修复 2 处后：注册数 5→4、hotel_search 结局白名单漏 success/empty）。
- py_compile 收编件/新配置/booking 消费点全过。

## 进行中 / 下一步

- B 系列：booking 删壳 + travel_v2 REST 三段端点（quote/confirm/status）+ 联动清理。
- U1-U4 依次；U5 rollinggo BLOCKED（联系人 york.lu@dida.com）。

## B 系列（booking 收编为预订子智能体 REST 化）——代码完成，验证中

1. **删壳（§4.1）**：booking 包 register/graph_builder/graph_node/graph_state 删除（事务机制 12 文件原样保留）；domains 注册 3 域；booking_prefilter/booking_pending_resolver/try_booking_pending（continuation+router_node 接线点）全退役；roundtable 预订人设/工具箱、capability 别名清空、KNOWN_DOMAINS、product_context 禁行根（travel_booking/booking 根）、TRAVEL_BOOKING_ENABLED 域开关与 is_booking_enabled、BOOKING_PENDING_RESUME_ENABLED（无消费方）删除；lock_domain 预订信号改内联同源词表（引导卡语义保留）。
2. **REST 化（§4.2）**：新增 `travel_v2/api/bookings.py`（POST quote / POST confirm / GET status，挂 /travel/v2/trips/{trip_id}/bookings/*）+ `travel_v2/models/booking.py`（请求模型 extra=forbid）；鉴权=网关身份 + 行程属主 fail-closed（TripService 属主校验，越权 404 不泄露存在性）+ booking/authorization 订单归属二次校验；provider off → quote/confirm 均 503 如实拒绝（不给无法执行的确认卡）；IN_DOUBT 话术走 reporter 白名单。router.py 挂载。
3. **联动（§4.4）**：evaluation ModuleKind 7（travel-booking 探针退役）+ builtin/release_gate/builder/cli/domain_contracts/runner/datasets/e2e 脚本删除；测试断言（domain_registration 全集=3、registry_overview 子流清零、runtime_registry、semantic_consistency _SUBFLOW_GRAPHS 空、router guard、entry_mode、legacy_route_writer 守卫清单收窄）；frontend-admin 三件；README/.env.example/evaluation-governance/evaluation-v2-api/ci-evaluation-environment；celery include 与 recovery beat 保留（052 四表与 rag_smoke 白名单不动）。
   - **对计划的偏离记录**：§4.4 原列「d2_two_hop 保留」——其实际依赖（graph_node/booking_pending_resolver/router 接线）全部是被删的聊天壳组件，语义不可保留；该文件随壳退役，其保护由 test_booking_rest_routes.py（REST 三段）与存活的 travel_pending 两跳测试承接。
4. **测试**：新增 `tests/travel/booking/test_booking_rest_routes.py`（三段正向 + 跨用户/跨行程 404 + 未认证 401 + provider off 503 + 参数 422；真 PG 门控 + 测试 ledger 隔离）。

### B 系列验证证据
- REST 路由测试 7 用例：首跑 1 failed（provider off 用例暴露 quote 未前置检查 provider 状态——修复：quote 入口加 off 前置拒绝）→ 复跑相关 3 用例 passed。

## U 系列（架构统一 + 数据源接入）——代码与测试完成

1. **U1 数据源层**：AmapPOIProvider（高德景点类目 110000|140000；评分/营业时间实回、unverified 标注、票价/时长不推断）+ 链账 `poi.search=("amap_live","seed")` + `assemble_poi_provider()` 链式装配接入 `travel_poi_search_tool`（同名/300m 去重并入，失败软降级留痕）；`travel.hotel.search` ToolSpec（READ，AGENT_LODGING）。
2. **U2 图节点**：`travel_lodging_expert`（weather 后 budget 前，域图 12→13 节点）+ `lodging_service`（CNY 单价换算/档位带/三态直通）+ auxiliary 扩展 `query_food`（高德商户 + L3 记忆饮食偏好确定性过滤）与返程衔接校验（车次 vs 末日末项结束时刻，只标注不改单）；canonical agents 7→8；turn_decision 契约与默认 prompt 同步。
3. **U3 V2 服务**：`POST /travel/v2/trips/{id}/auto-optimize`（只读编排：餐饮评分降序/住宿价格升序 top3 + 逐类软失败披露 + apply_hint；零写入）+ `BookingConfirmGate.tsx`（两段式确认卡：报价→确认→状态；取消/失败如实）挂 `TravelSearchPanel` 酒店卡 + travelV2 API 客户端六函数。
4. **U4 Skill 层**：`travel.food_search` / `travel.hotel_search` / `travel.booking_quote` 三 Skill（Tool 层 food/hotel/booking 包装 + capabilities.yaml + spec 治理面 travel.booking.quote WRITE）；skills 注册守护与 output_type 清单同步；rag_smoke 定向清单 +4 测试。
5. **U5 rollinggo**：BLOCKED（上游库存权限，联系人 york.lu@dida.com）；能力账 hotel/flight.search 注记滚动更新，fake 契约不变。

### U 系列验证证据
- U1/U2 契约：`pytest tests/travel/test_amap_poi_provider.py test_lodging_expert.py` → 15 passed + 1 skipped（live 冒烟 env 门控）。
- 结构回归：test_travel_graph（stage 链 weather→lodging→budget）/FROZEN_ORDER/canonical agents/agent_inventory 全绿（18+163 passed 一轮修复后）。
- U4：`pytest backend/tests/skills test_layer_consistency.py` → 112 passed；capabilities 注册 `travel.food_search/hotel_search/booking_quote` 三键确认。
- U3：auto-optimize 3 passed；前端 `vitest run src/components/travel src/api` → **405 passed**；`tsc --noEmit` 16 错误=main 既有基线（零新增）。
- 收官合并验证：`pytest travel graph/governance/lodging/amap/hotel_search/auto_optimize/booking REST` → **87 passed, 1 skipped**（含真 PG 上 quote→confirm→status 假全链）。

## 阻塞 / 移交下一会话（2026-11-01 用户指令：剩余工作交接）

### 工作树与分支
- Worktree：`D:\Program Files\workplace\agent-wt-travel-consolidation`；分支 `feat/travel-domain-consolidation`；HEAD=三笔收编提交（cc6a707 D 系列 → 6f37d03 B 系列 → e1a0f32 U 系列），基线 origin/main@262e839（PR #187 合并后）。**未合并 main**——待用户确认后走 PR（建议三笔一起一个 PR，标题「travel 收编重构：commerce/booking 域图壳删除 + 架构统一 + 真实数据契约测试」）。
- `.env` 已从主仓库拷贝（含 AMAP_KEY，不入库）；`frontend/node_modules` 为指向主仓库的 Junction（只读复用，前端测试直接可跑）。
- 原型服务器 `python -m http.server 8124`（pid 16916，另一会话 8:28 起的）仍在跑——用完请停。

### 剩余工作（按优先级）
1. **booking lifecycle 慢套复验**：`pytest backend/tests/travel/booking/test_booking_lifecycle.py test_booking_state_and_identity.py test_stop_e_manual_resolve_ledger.py`（真 PG + advisory 锁；本会话两次因与其他会话并发测试抢 `ci_travel_booking_tests` 锁超时，未取得最终绿——REST 三段已独立绿，lifecycle 预期不受收编影响但需实证）。
2. **收编分支重建本机服务验证**（AGENTS.md 纪律）：`devctl.bat rebuild` 需用包含本分支代码的目录重建——当前共享栈仍跑旧代码；重建后 `/health` 应无 `travel_commerce` 组件、`/agents` 应无两张子流图。并行会话占用共享栈时先协调。
3. **真实高德样本录制**：BLOCKED-IN-ENV——本沙箱 httpx 0.28.1 对 restapi.amap.com TLS EOF（urllib 正常，传输层问题非本任务引入）。在正常网络环境跑 `TRAVEL_AMAP_LIVE_SMOKE=1 pytest backend/tests/travel/test_amap_poi_provider.py::test_live_amap_search_record_shape` 录制样本→脱敏入库为 fixture。
4. **PR 合并**：CI 起来后核对 rag_smoke 新增四测试在 CI 通过；合并后删除 `travel-platform-proto` 之外的临时进程、fast-forward 主仓库本地 main（当前本地 main@0d848a6 含未推送的 59bcde9，已随 PR #187 落库）。
5. **§8 收尾项**：travel_commerce 残留仅剩 055 迁移注释（历史不可改）与任务文档自身（合理）；travel_booking 残留均为白名单面（booking 包/REST/052/recovery/env/负向断言）。

### 总计划与验收对照（DOMAIN-CONSOLIDATION-PLAN.md §8）
- [x] travel_commerce/travel_booking 域图注册/预滤器/续填/圆桌/评测零残留（grep 核对，例外=迁移历史注释+任务文档）
- [x] travel 域图 12+1（lodging）节点，stage_targets 结构回归通过（13 节点）
- [x] 契约测试矩阵 §6 主要行落位（POI/Amap 15+1、Lodging 10+6、Booking REST 7、AutoOpt 3、Skills 112；Dining/Intercity 由 auxiliary 用例承接；真实样本录制 BLOCKED-IN-ENV 移交）
- [x] 原型场景抽样：三轮澄清 PASS / 生成含住宿档位 PASS / 工作台+自动优选+时区警告 PASS / 酒店 rollinggo:mcp 披露 PASS；food 偏好过滤与返程车次由单测承接（原型交互点测未逐一走完）
- [x] V2 API 行为回归（前端 405 全绿 + travel_v2 定向）+ booking REST 三段集成（真 PG 假全链）
- [ ] booking lifecycle 慢套最终绿（移交 1）
- [ ] 本机重建健康验证（移交 2）

### 其他会话并行事实（下一会话开工前重读）
- 本会话期间另一会话在主仓库跑 rag 全套与 booking reconciliation 测试（共享 dev PG 5433 + booking advisory 锁）——booking 慢套复验前先确认无并发 pytest。
- 本地主仓库 main@0d848a6 落后远端且 59bcde9 已随 PR #187 落库——合并后主仓库需 fast-forward 对齐（涉及主 worktree，属用户操作）。
