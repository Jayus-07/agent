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
- [x] booking lifecycle 慢套最终绿（收尾会话 2026-11-01：34 passed，见下）
- [x] 本机重建健康验证（收尾会话 2026-11-01：三项全过，见下）

### 其他会话并行事实（下一会话开工前重读）
- 本会话期间另一会话在主仓库跑 rag 全套与 booking reconciliation 测试（共享 dev PG 5433 + booking advisory 锁）——booking 慢套复验前先确认无并发 pytest。
- 本地主仓库 main@0d848a6 落后远端且 59bcde9 已随 PR #187 落库——合并后主仓库需 fast-forward 对齐（涉及主 worktree，属用户操作）。

---

# 收尾会话执行记录（feat/travel-domain-consolidation，2026-11-01 续工）

> HEAD 起点 69381be（五笔收编提交），收尾新增 1 笔（207552c AMAP 样本）+ 本文档提交，共 7 笔走一个 PR。

## 1. booking lifecycle 慢套复验——34 passed，附带定位测试环境根因

- `pytest backend/tests/travel/booking/test_stop_e_manual_resolve_ledger.py` → **2 passed in 3.64s**；
  `pytest backend/tests/travel/booking/test_booking_lifecycle.py backend/tests/travel/booking/test_booking_state_and_identity.py` → **32 passed in 23.78s**（真 PG 5433 + advisory 锁 ci_travel_booking_tests 正常持有）。
- **根因（重要，影响所有宿主机直连 PG 的测试）**：`.env` 的 `PGHOST=localhost`，Windows 下 localhost 先解析 IPv6 `::1`，而 Docker Desktop 端口映射只绑 `127.0.0.1:5433`——`::1:5433` 被拒绝前还有约 2s 延迟（本机代理客户端 TUN 干扰），psycopg 每次 connect 都先在 `::1` 上空耗，33 用例累计 65 分钟且部分连接直接挂到 pytest-timeout。**规避：跑测试时 shell 设 `PGHOST=127.0.0.1`**（load_dotenv 不覆盖已存在环境变量，零代码改动）。修复前后对比：同文件从 150s 超时挂死 → 3.64s 全绿。
- 另发现：早前记录的「并发抢锁超时」之外，本套真正的耗时大头是上述连接空耗。

## 2. 本机重建健康验证（devctl.bat rebuild @ 收编 worktree）——三项全过

- rebuild 后全容器 healthy（app/worker/frontend/frontend-admin 均以本分支代码重建）。
- `/health`：status=ok，**无 travel_commerce / travel_booking 组件**（仅合法的 travel_providers）。
- `GET /agents`（:8000 直连 + :9080 网关双路）：**无子流域图**——domain_graph=3（travel/customer_service/selection_funnel，subflow 全 null）；响应中 travel_booking 字样唯一出现处是新 Skill `travel_booking_quote_skill`（U4 合法面）。
- 网关链路 count=27（orchestration 8 / skill 16 / domain_graph 3）。

## 3. AMAP 真实样本录制入库——BLOCKED-IN-ENV 解除（提交 207552c）

- **上会话 TLS EOF 根因查明（非沙箱、非代码问题）**：Windows 下 httpx `trust_env=True` 经 `urllib.request.getproxies()` 拾取**注册表代理**（127.0.0.1:7897），并把 https 流量代理解析为 `https://` scheme——对代理端口做 TLS 握手，代理非 TLS 服务 → `EOF occurred in violation of protocol`，100% 复现。`trust_env=False` / 显式 `http://` 代理 / `NO_PROXY=restapi.amap.com` 全部正常。控制变量实验（同进程交替 trust_env）与多轮探针（d:/tmp/probe_amap*.py）已闭环。
- `test_amap_poi_provider.py` 修复两处隔离回退：live 用例恢复 env 真 key（autouse `_quarantine_env` 会打桩假 key——上会话从未到达的失败层）；patch `httpx._utils.getproxies`（from-import 绑定处）强制直连。
- **真实样本已替换 schema 构造样本**：`_AMAP_RECORD` = 2026-11-01 厦门「景点」检索首条真实返回（筼筜湖，normalize_merchant 原样输出；公开 POI 事实，无凭据/个人敏感字段，tel 为空）。
- `TRAVEL_AMAP_LIVE_SMOKE=1 pytest backend/tests/travel/test_amap_poi_provider.py` → **7 passed**（含 live 真实调用）。

## 4. MCP 管理端治理配置测试（a–e 逐项，全部走通 + 测试产物已清理）

前置：注册临时账号 mcp_gov_tvsi1w → DB 提权 admin → JWT（用后已停用+logout 吊销）；走 :9080 网关 enforce 链路。API 响应原始证据存 d:/tmp/mcp_gov_evidence_{a,b,c,d}.json（凭据零入库零文档）。

- **a) 登记/查看**：POST /admin/mcp/connections 登记 `12306-govtest`（http://mcp-12306:8000/mcp，streamable_http/private）→ status=connected；discover 枚举 7 工具（query-tickets/query-transfer/search_stations 等），动态 capability 以 `mcp.<conn_slug>__<tool>` 形态出现（`mcp.t12306_govtest__query_tickets`——数字开头 slug 自动加 t 前缀）；review approve 后 status=approved，overview `active_tools` 7 项全生效。
- **b) 授权流**：`GrantRequest.product_key="travel"` → **422 INVALID_PARAM**（模型 Literal 白名单仅 `["agent","customer_service"]`——**travel 产品键扩展是待用户决策的公共契约变更，未擅自扩**）；agent 口径授权成功（granted=true, granted_by=user:1318）；对账：工具详情 grants 视图 + overview grant_count + `GET /capabilities`（count=20/routed=17/skills=15/unregistered=[]零漂移）。**授权闭环黄金证据**：授权后 agent 身份 test-invoke 成功 → revoke 后同身份返回 `product_capability_denied`「当前产品入口不可执行该能力」→ regrant 恢复。
- **c) 12306 灰度闭环**：开启+审批+授权后 test-invoke（product_key=agent）返回**真实车次**：福州→厦门 2026-11-10 共 217 趟（D3333 06:43→08:20 二等座有票等，含余票字段）；disable 连接后再查返回 **409「工具未在运行时生效（连接启用=False），无法测试」**——fail-closed 如实披露，无任何模拟数据；参数校验先于出网（缺必填字段 → invalid_param）。
- **d) rollinggo（事实修正）**：登记 https://mcp.rollinggo.cn/mcp（static_bearer/public）→ status=connected；连接 test ok=true 枚举 3 工具（searchHotels/getHotelDetail/getHotelSearchTags）；searchHotels 审批+授权后**真实调用成功**（originQuery/place/placeType 构参）：code=2000「酒店搜索成功」，返回上海真实酒店报价（上海裕景大饭店 4 星，570 CNY/晚 hasPrice=true）。**上会话「库存权限未开通 BLOCKED」对搜索/查价面不成立**——BLOCKED 结论需向上游（york.lu@dida.com）确认的面收窄为**预订写操作面**（管理端 test-invoke 仅限 read，写面必须走真实业务链路确认门，本轮未触碰）。结束态：连接 disable 保持 BLOCKED 面关闭。
- **e) 留痕与清理**：授权全部 revoke（DB 回查 revoked=t）、两条 govtest 连接 disabled（12306 恢复「已接默认关」语义）、测试账号 status=0、会话 logout 吊销、本地 token 文件已删除。已知清理边界：govtest 连接因存在已审批工具被引用保护（DELETE 409）而保留（disabled 态），后续正式启用时直接改名/替换即可。

## 5. 收尾结论

- §8 全局验收清单**全部勾选**（见上）。残留复核：`travel_commerce` 仅剩 test_domain_semantic_consistency.py:449 负向守护断言；`travel_booking` 均为白名单面（booking 包本体/REST/recovery/052/env/新 Skill 名/负向断言）。
- PR：7 笔提交（D/B/U 三系列 5 笔 + AMAP 样本 1 笔 + 本文档）走单 PR 合并，CI 首跑覆盖 rag_smoke 新增 4 测试路径（hotel_search_service/amap_poi_provider/lodging_expert/tool_governance）。

---

# 投产施工 P1–P5 执行记录（feat/travel-proto-alignment 系，2026-11-01）

> Worktree `agent-wt-travel-proto`；每阶段独立 commit + 独立 PR，全部已合并 main。
> 阶段 PR：P1 #205（1c1f73f）→ P2 #208（946fe04）→ P3 #209（0b41502）→ P4 #210（c140367）；
> 验收发现修复：#212（reporter 住宿段 c7d07fc）、#213（auto-optimize 传参 6ff82f7）、#214（selection_id 生成 cd0ea2d）。

## P1 rollinggo live 接线（#205，后端）

- **管理端治理全链**：新建正式连接 `rollinggo`（https://mcp.rollinggo.cn/mcp，static_bearer/public，凭据经管理端加密入库，零入代码/文档/日志）→ discover 3 工具 → searchHotels 审批（read/R1/无确认，domain 未分类——agent 产品为运行时唯一 travel 场景授权口径，任务纪律未扩 GrantRequest.product_key）→ agent 授权。**授权闭环黄金证据**：授权后 product_key=agent test-invoke 真实成功（上海酒店 code=2000）。
- **live 适配器**（rollinggo_mcp.py）：经 mcp_registry 快照 + mcp_dynamic 统一执行核心出网（零第二套 MCP 路径）；价格按总价契约诚实映射（message 显式总价优先，兜底每晚价×晚数）；未报价过滤；多间房/携儿童无法如实表达显式拒绝；运行时不在场=disabled 绝不回退 fake。
- **真实样本 fixture**：授权链路录制 searchHotels 真实返回（去 nextPageToken 脱敏；酒店名称/价格/地址/坐标为公开商业事实保留）。
- **测试**：适配器契约 8 用例 + 三态断言同步两处 + live 冒烟（TRAVEL_ROLLINGGO_LIVE_SMOKE 门控真实出网）；rag_smoke 定向清单登记。
- **运行态验证**：rebuild 后 app 容器内 search_hotel_offers → status=success / source=rollinggo:mcp / 10 offers（真实报价）。
- 清理：临时 admin 账号停用+logout 吊销；含凭据临时文件删除；`rollinggo` 连接保留（正式接线）。

## P2 F18 card 事件通道（#208，后端+前端）

- 后端：`travel/core/cards.py` ToolCardPayload 契约（类目白名单 train/hotel/food/poi、条目≤8、缺失不补造）+ `run_checked` 成功路径单一接线点（白名单类目+preview 非空才发卡；失败/空不发）。契约测试 8 用例。
- 前端：`tool.card` 事件类型 + parseTravelToolCard（形状不符返回 null）+ ToolCardBlock 共享渲染（复用 V1 ToolPreviewBody）；TravelChatPage 按 turn 收集内嵌渲染、动作直通聊天管线；TravelV2TripConversation 只读问答轮收卡、动作直通 ai-edit 既有写路径。

## P3 前端小项（#209）

- **F04**：工作台双 header「自动优选」入口 + AutoOptimizePanel 建议确认卡；批量选入=既有单条 selection 端点逐条幂等（expected_revision 链式推进）；后端建议行与搜索 Tab 同源同构（补齐全套选入快照字段）。
- **F02**：TravelChatPage loading 气泡接 ToolProcessRows 折叠行。
- **F05**：详情卡「查看备选地点」内联候选 + 二次确认替换 + 既有 applyEdit(replace_place) 单一写路径（V1 CandidatesPanel 为已退役 V1 端点耦合件，不引入）。

## P4 F17 目标态布局（#210）

- 工作台三列重排：行程时间轴（左）→ 地图列（中：概览卡 + 天数条叠地图上方）→ AI 助手（右列）；grid order 重排、现有 V2 组件本体零改动；移动端不受影响。

## P5 验收（运行态逐场景点测 + 对账）

- **重建**：共享栈从本 worktree rebuild 4 次（每轮修复后），全容器 healthy；验证一律 `PGHOST=127.0.0.1`。
- **逐场景点测（:9080 网关，临时账号 P5_JWT，走真实 JWT 链路）**：
  - 场景1 三轮澄清 **PASS**：needs_clarification + 3 个结构化选项（规划厦门/杭州/福州）。
  - 场景2 生成含住宿档位 **PASS**（修复 #212 后）：带日期消息 → 3 天行程 + 「## 住宿建议」段（如家商旅 ¥206/晚 等，`rollinggo:mcp` live 真实数据）；无日期时住宿查询如实 skipped（无段），符合契约。
  - 场景3 模板复制 **PASS**（xiamen-sea → trip f83e3e85…）。
  - 场景4 自动优选+批量选入 **PASS**（修复 #213/#214 后）：destination=厦门、3 餐饮（amap）+3 住宿（rollinggo:mcp ¥184/晚起）；selections/meals 选入 saved=True revision 1→2。
  - 场景5 预订确认门 fake 全链 **PASS**：quote（554.01 CNY awaiting_confirmation）→ confirm（booked, provider_order=fbk-native-0001）→ status（booked, order_id=bo_5b63…）。
  - 场景6 时区警告 **PASS**：travelV2PlannerAdapter 单测断言未确认时区 → UTC 占位 + `timezone_unconfirmed` issue（test:83-94）；工作台消费区块已有。
  - 场景7 live 酒店报价 **PASS**：场景2/4 住宿建议均为 rollinggo:mcp 真实报价（P1 运行态验证同源）。
- **验收发现并修复的 3 个真实缺口**（各自独立 PR）：
  1. #212：lodging 专家执行但 reporter 无住宿段——原型「住宿建议」语义未达用户面；补 `_render_lodging_picks`。
  2. #213：auto-optimize 端点把行程行当 TripDocument 传 → destination 恒空、建议恒空（U3 仅 service 级测试）；改传 document + 真 PG API 级回归。
  3. #214：餐饮建议 selection_id=None（live_search 行不带该字段）→ 无法选入；缺失时按搜索 Tab 同一 stable_selection_id 规则生成。
- **F18 tool.card 运行态**：生成流（场景1/2）未触发对话查询类目，事件通道由契约测试（8 用例）承载；对话查询触发时事件经既有 SSE 通道自动到端（seq/run_id 注入复用）。
- **F14 预订写面**：如实 BLOCKED——rollinggo 预订/库存写操作未接（fail-closed），预订确认门走 fake（上游权限，联系人 york.lu@dida.com）；确认门机制本身运行态全链已验证（场景5）。
- **治理面一致性**：`rollinggo` 连接 enabled + searchHotels approved + agent 授权存续（正式接线态）；12306-govtest/rollinggo-govtest 两条 govtest 连接保持 disabled；MCP 治理视图与运行态一致。
- 清理：P5 临时账号停用+logout 吊销；测试行程归档（保留审计）；凭据临时文件全部删除。

## F01-F19 对账矩阵（投产施工后终态）

| # | 功能 | 终态 | 证据 |
|---|---|---|---|
| F01 | 三轮槽位澄清（结构化选项） | ✅ | C2（#187）+ 运行态场景1 PASS |
| F02 | 气泡内工具过程行折叠 | ✅ | P3（#209）ToolProcessRows 接入 V2 |
| F03 | 两段式草稿预览保存 | ✅ | C1（#187） |
| F04 | 自动优选+批量选入 | ✅ | P3（#209）+ 修复 #213/#214 + 运行态场景4 PASS |
| F05 | 卡片内联备选+二次确认替换 | ✅ | P3（#209）replace_place 直通 |
| F06 | 查询三 Tab | ✅ | 已对齐（既有） |
| F07 | 预订确认门 | ✅ | 机制既有 + P5 场景5 fake 全链 PASS |
| F08 | 版本历史/恢复/ICS/分享 | ✅ | 已对齐（既有） |
| F09 | 写状态三态/幂重试/CAS | ✅ | 已对齐（既有） |
| F10 | 模板目录/复制 | ✅ | 种子（#187）+ P5 场景3 PASS |
| F11 | 天气-行程联动 | ✅ | 已对齐（既有） |
| F12 | 地图 | ✅ | 已对齐（既有） |
| F13 | POI 实时数据 | ✅ | AmapPOIProvider（#201） |
| F14 | 酒店 rollinggo 直连 | ⚠️ 搜索/查价 ✅；**预订写面 BLOCKED** | P1（#205）搜索/查价 live 全链 PASS（运行态场景2/4/7 真实报价）；预订写操作未接（fail-closed，上游权限 york.lu@dida.com） |
| F15 | 会话关联/通知条 | ✅ | 已对齐（既有） |
| F16 | 动态增删天 | ✅ | 已对齐（既有） |
| F17 | 目标态布局 | ✅ | P4（#210）+ 原型对齐修正（#217）：地图+概览左列（天数条地图上方、概览卡地图下方）、时间轴中列、助手右列 |
| F18 | SSE card 结构化事件 | ✅ | P2（#208）tool.card 通道 + 契约测试 8 用例 |
| F19 | 时区未确认警告条 | ✅ | adapter 单测断言 + 工作台消费（P5 核对） |

## 原型逐场景点测（2026-11-01 补做，用户移交原型文件后）

原型位置：`E:\.zcode\v2\visualizations\c052931e…\travel-platform-proto.html`（含 HANDOFF.md 交接文档）。浏览器实测（http.server 8124 + IAB，1440×900）：

- **7 场景全部 PASS**：三轮澄清（结构化选项点击联动槽位）/ 生成含住宿档位（¥388/晚 段落）/ food 偏好查询（推荐+选入按钮）/ 自动优选报告（餐饮/住宿/交通/天气四类 + 完成披露）/ 确认门 fake 全链（报价 → TTL → 确认 → 已预订+订单号）/ 时区警告条（保存 v1 自动挂）/ 酒店报价（候选+每晚价+确认门披露）。
- **发现并修正一处布局差异**：原型布局（HANDOFF §5.1 用户指定）=「地图+概览在左列（天数条地图上方、概览卡地图下方）、时间轴中列、助手右列」；P4 实现为时间轴左/地图中/概览卡在天数条上方。修正 PR #217 已合并（列序对齐 + 地图列内部重排），rebuild 后生效。
- 原型语义与平台实现对账结论：本轮 7 场景语义（澄清/住宿档位/优选/确认门/时区/报价/card 通道）平台侧均有对应运行态证据（见上文 P5 点测），原型与平台**语义一致**；差异仅在原型为演示引擎（固定剧本/模拟数据），平台为真实链路。

## rollinggo 预订能力核查（回应「好像有下单预订功能」）

**用户决策（2026-11-01）：先落地「跳转型预订」**（PR #219 已合并）——透出 Provider 的 bookingUrl（已过 deeplink 白名单门），住宿建议（chat reporter 段 + 工作台自动优选面板）渲染「去预订」链接跳转 rollinggo 官方页面，用户自行完成下单；平台不代订、不产生订单回执，如实披露。确认门（fake 全链）保持不变，二者语义互补；平台内闭环预订待上游在 MCP 开放预订工具后切 provider。运行态验证：app 容器内 lodging options 10 条均带 rollinggo.cn 官方页链接，reporter 渲染 [去预订] + 披露，auto-optimize 透传 ✓。

- 直连 MCP 端点（含探测 /mcp/flight、/mcp/order、/mcp/booking 等路径）：仅 3 个查询工具（searchHotels/getHotelDetail/getHotelSearchTags），**无预订/下单类工具**。
- rollinggo 平台本身有预订业务（每条酒店结果的 bookingUrl 指向其 H5 下单页 rollinggo.cn），但不在 MCP 接入点上——「有预订功能」≠「MCP 可下单」。F14 平台内闭环预订仍 BLOCKED（需上游在 MCP 开放预订工具）；如需「跳转型预订」（透出 bookingUrl 引导用户自行完成），deeplink 白名单已配 rollinggo.cn（P1），属可选增强、需用户决策。

## 接入状态（2026-11-01）

- **本机共享栈（8080/9080 运行环境）= 已接入 live**：P1–P4 + 修复 #212/#213/#214/#217/#219 全部合并 main 并 rebuild 生效；`TRAVEL_COMMERCE_PROVIDER_MODE=live` + `TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS=rollinggo.cn` 已同时配置在 worktree 与**主仓库** .env（防从主仓库 rebuild 时回退 fake 的用户可见回归）。running 证据：场景2/4/7 真实报价、预订链接透出（容器内验证）。
- **真实服务器（部署/prod-4c8g）= 未部署**：本任务范围为本机共享栈；上服务器需按 部署/prod-4c8g/README.md 流程执行（其 .env 需单独配置 live 开关与白名单，凭据走管理端登记）。
- F14 终态：搜索/查价 live ✅；预订动作面 = 跳转型预订 ✅（平台不代订）+ 确认门机制 ✅（fake）；**API 闭环预订仍 BLOCKED**（上游 MCP 未开放写工具）。