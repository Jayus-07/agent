# 旅游 AI Agent 平台 V2｜统一产品与系统总设计书

> **文档版本**：v1.0-RC1（待确认，不代表技术验收完成）  
> **编制日期**：2026-10-12  
> **目标仓库**：`Jayus-07/agent`  
> **建议正式位置**：`docs/domains/travel-v2/product-design.md`  
> **当前状态**：产品决策已统一；后端另一会话正在施工，接口差距、数据迁移和完成情况待最后对账；**本稿作为 v1.0-RC1 产品设计评审基线；允许前端 FE-0 审计，不授权据此直接扩大后端施工范围。**
>
> **唯一性说明**：本文件合并此前 v0.1–v0.6 的有效产品决策。重复描述、旧版 Active/Draft、“应用草案”交互、无源实时数据、假完成状态均不再作为 V2 要求。各条规则只在一个主章节定义，其他章节仅引用对应决策 ID。实际源码、Schema 和测试始终是“当前已实现状态”的事实源；本文件描述最终应达到的产品行为。

---

## 0. 一页执行摘要

**产品一句话**：用户通过 AI 对话获得旅行灵感、规划行程，并能把真实景点、美食、住宿、铁路与地图数据融入一份可编辑、可恢复、可分享的正式旅行计划。

**核心体验**：聊天主入口 + 结构化 TripBrief + PC 三栏工作台 / 手机单列体验 + Provider 独立查询与行程联动 + 手动及 AI 修改自动保存 + 追加式 Revision 历史 + 固定 Revision 分享。

**团队分工**：
- 本设计会话：产品交互、跨模块契约、优先级与验收口径。
- 另一后端会话：现有后端改造，按当前独立任务继续；交付 Commit、接口 Schema、测试证据后再对账。
- 前端 Codex：**可以先启动独立前端批次**，仅处理前端已有能力收口、响应式、用户状态与测试。后端变更点仅使用边界清晰的 API Adapter、状态类型和隔离测试 Fixture，不假装真实联调已完成。

**本期不交付**：真实预订、支付、出票、退款；没有真实 Provider 的实时价格/余票/房态；重建主 Agent Runtime、Tool Governance、V1 版本账本；独立的酒店/动车/美食 Agent；旧版“应用草案”机制。

**交付策略**：前端先审计→做不依赖后端变化的用户界面与状态→组件/定向测试；待后端契约冻结后联调→跑真实网关/数据库/Provider E2E→发布验收。开发不需要影子双跑或长时间灰度，但任何发布都必须具备安全回退与数据迁移保护。

---

## 1. 产品目标、用户价值与边界

### 1.1 用户主场景

| 编号 | 真实用户愿望 | 平台承诺 |
|---|---|---|
| S1 | “周末从福州出发去哪？” | 探索候选、原因和必要追问；目的地未定不创建虚假行程 |
| S2 | “去杭州玩三天，两大一小，慢游” | 抽取 TripBrief，必要澄清，生成按天行程和明确未知事实 |
| S3 | “第二天下午太赶，保留灵隐寺” | 有范围约束的 AI 修改；校验成功即保存，失败可恢复追问 |
| S4 | “找西湖附近的杭帮菜/酒店” | 无须先建 Trip 就可只读搜索；选中后才进入行程写入 |
| S5 | “选这趟动车作为去程” | 绑定具体日期、站点、车次，检查接驳冲突；不表示已购票 |
| S6 | “恢复昨天的版本，分享给朋友” | 追加新的恢复 Revision；分享固定快照且可撤销 |
| S7 | “手机上接着修改” | 同一 Trip、同一正式 Revision；移动端不复制第二份业务数据 |

### 1.2 系统边界

- **静态知识**：RAG 或已治理知识源提供背景解释，不替代实时营业时间、价格、酒店库存、班次。
- **动态事实**：Provider/Service 经统一结果与 Evidence 契约返回；来源、观测时间、可信状态与有效期独立呈现。
- **AI**：解释需求、问答、提出有约束的结构化编辑意图；不直接写数据库或宣称事务成功。
- **确定性服务**：授权、幂等、CAS、Validator、路线/预算计算、Revision 持久化。
- **交易边界**：选择酒店/动车只是计划安排，不等于真实预订、付款或出票。

### 1.3 产品成功判据

关键路径达到：可理解→可规划→可核实→可修改→可恢复→可分享；界面所称“成功/已保存/实时”与后端及 Provider 事实严格一致。

---

## 2. 冻结产品决策与不可违反的不变量

| ID | 唯一决策/不变量 | 备注 |
|---|---|---|
| D-01 | Provider **独立查询 + 行程内嵌** 双入口，复用同一查询服务、数据归一与 Evidence | 没有 Trip 也能查；查询本身只读 |
| D-02 | AI 修改默认最小爆炸半径，优先局部；必要时在用户明确授权范围内重排 | 不隐式跨天搬运/删除未提及 POI |
| D-03 | 手动和 AI **在服务端完整校验及事务提交成功后自动保存正式行程** | V2 无“应用草案”；校验冲突时不保存 |
| D-04 | 正式行程的单一事实源是 **Trip + 追加式 Revision** | 不恢复 V1 Active/Draft，也不建立第二套版本账本 |
| D-05 | Undo、Redo 和历史恢复统一执行 Restore，产生新的 Revision | 不是完整 Event Sourcing，不建设单独 Redo 栈 |
| D-06 | 分享固定创建分享时的 Revision | 后续 Trip 修改不改变分享内容 |
| D-07 | 搜索候选、用户选定快照、后续 live 观测是不同语义 | 动态信息变化不能静默覆盖已保存的选择 |
| D-08 | 查询与问答不得产生 Revision；明确“加入/替换/编辑/恢复”才进入写路径 | 多入口同一写入门禁 |
| D-09 | Provider 失败、过期、无数据必须如实显示；Mock/Seed 不得冒充 live | 无法核实的动态字段显示未知/不可用 |
| D-10 | 编辑冲突三级：INFO、WARNING、BLOCKED | INFO/WARNING 可按策略提交，**硬冲突 BLOCKED 不提交** |
| D-11 | BLOCKED 进入带结构化冲突详情的 `pending_resolution`，等待用户明确选择后重新校验 | 挂起期间正式 Revision 不变 |
| D-12 | 交通接驳采用可配置缓冲；未知耗时不得当作零或无条件保证可行 | 可保守估算并标注，硬时间约束仍需足够证据 |
| D-13 | SSE 显示真实运行进度和提交回执；正式 Trip 以服务端权威 REST 事实核对为准 | 不把 SSE `done` 等同于保存成功 |
| D-14 | 客户端对每次逻辑写入生成并保留幂等标识，重试保持同一键及相同内容 | 采用现有 `Idempotency-Key`；操作状态接口待对账 |
| D-15 | 时间按 LocalDate、LocalTime/相对区块、Zoned Instant、Duration 分开建模 | 保留 IANA 时区；时间字段间必须表达同一瞬间 |
| D-16 | 任何正式 Trip 写路径在事务提交前通过同等完整性约束 | 创建、整文档替换、结构化编辑、选入、模板、恢复均不能绕开 |
| D-17 | 当前前端、后端能力优先 REUSE/FIX，缺口才 BUILD | 禁止平行 Runtime、重复 API、重复数据表 |
| D-18 | PC/手机共用一份服务端 Trip，前端临时状态不得冒充正式版本 | 切换 Trip/断线/并发不允许旧结果覆盖当前页面 |

**冲突处理示例**：“把上午灵隐寺移到下午”碰到下午已有西溪湿地。默认不能把西溪湿地顺延至第三天；也不能将硬重叠保存为“仅警告”。应返回具体冲突、可执行选项（调整同日下午、替换、改时段或取消），待用户授权后重新校验并提交。

---

## 3. 产品信息架构与页面行为

### 3.1 页面清单

| 页面 | PC | 手机 | 核心内容 |
|---|---|---|---|
| 首页 | 对话入口 + 服务入口 + 模板 + 最近行程 | 单列首页，底部导航 | 规划、探索、查美食/酒店/动车 |
| AI 会话 | 工作台左栏/独立聊天 | 聊天主页面 | TripBrief、追问、真实运行/Tool 进度、回复 |
| Trip 工作台 | 聊天 + 中间结构化 Tab + 右侧地图/详情 | 行程详情与聊天单页切换 | 行程、餐饮、住宿、交通、地图与版本 |
| 服务搜索 | 工作台 Tab/独立搜索 | 搜索列表→详情 | 无 Trip 只读；有 Trip 可“加入/替换” |
| 我的行程 | 列表管理 | 列表 | 打开、复制、重命名、归档、分享 |
| 模板中心及预览 | 模板卡/详情 | 模板卡/详情 | 复制为独立 Trip，不变更源模板 |
| 历史版本 | 侧栏/对话框 | 历史页/底部抽屉 | Revision 摘要、必要 Diff、恢复 |
| 分享页 | 固定 Revision 只读 | 固定 Revision 只读 | 受控分享，不暴露内部聊天或 Trace |

### 3.2 PC 三栏与移动端原则

- PC 左侧：AI 交互、TripBrief 轻量条件、流式阶段与工具结果；中间：`行程 | 美食 | 酒店 | 车次`；右侧：地图、路线或选中项目详情。
- 中间每日时间轴只展示当天景点/活动及餐饮；**住宿安排**以入住区间组织；**城际交通**在去程/返程或日期交通卡展示，不全部塞进景点时间轴。
- 手机首页：AI 输入、快捷查询、模板、最近 Trip，底部 `首页/行程/发现/我的`；手机 Trip 页面优先时间轴，聊天和查询全屏/抽屉切换。
- 不做繁复技术仪表板：默认仅展示当前阶段与最近结果，需要时展开工具执行详情；不泄露模型原始思维链或敏感参数。
- 不再出现 V1 “应用草案”按钮。以`校验中/保存中/已保存/保存失败/版本冲突/待澄清/状态待核实`替代。

### 3.3 页面动作 → 业务语义

| 用户动作 | 前端反馈 | 是否改正式 Trip |
|---|---|---|
| 输入探索请求 | 候选、理由、必要追问 | 否 |
| 提交完整规划 | 真实进度；创建成功后进入 Trip | 是（首次创建） |
| 点击日期、地图、查询 Tab | 切换视图 | 否 |
| 查询餐厅/酒店/动车/景点 | 搜索状态、详情、Evidence | 否 |
| 选择“加入第 N 天午餐” | 校验中→正式 Revision 或失败 | 是，校验通过才写 |
| “选为住宿” | 绑定入住区间、注明未预订 | 是，校验通过才写 |
| “选为去程/返程” | 绑定班次和站点、注明未购票 | 是，校验通过才写 |
| AI 问答“我第 2 天去哪” | 只读回答及引用 | 否 |
| AI 修改“第二天轻松些” | 仅目标范围变更；自动保存或待澄清 | 条件满足时写 |
| 撤销 / 恢复历史版本 | 新恢复 Revision、Diff、成功消息 | 是 |
| 分享 / 导出 | 固定当前选定 Revision | 不更改正式行程内容 |
| 浏览模板 / 使用模板 | 先预览；使用时创建新 Trip | 仅创建独立 Trip |

### 3.4 状态与空态

每个页面必须区分：loading、empty、success、partial、unavailable、permission_denied、error、retryable、cancelled。Provider 成功但结果为空不显示为系统故障；保存失败不在中间区留下假正式行程。手机端页面切换必须保留当前 Trip 与选中日期，并正确处理网络中断。

---

## 4. 七 Agent 目标职责与调用边界

> 本节描述目标角色，不宣称当前七个 Agent 全部已按设计接线。另一后端会话交付后需依据 `graph_builder.py`、Registry 和 Trace 对账。

| 角色 | 职责 | 不得承担的职责 |
|---|---|---|
| Travel Supervisor | 意图分诊、执行状态调度、范围与护栏 | 直接操作数据库、伪造 Provider 成功 |
| Requirement | 旅行需求抽取、TripBrief、歧义与缺口追问 | 为缺失条件编造用户选择 |
| Research | 目的地、地点、天气、知识及事实证据 | 将 RAG 内容当实时库存和车次 |
| Planning | 每日骨架、必须去/避开等约束 | 在无事实的情况下伪造地点 |
| Optimization | 顺路排序、时段、缓冲、预算、局部调整 | 擅自跨天改变未授权固定计划 |
| Travel Assistant | 旅行问答、查询说明、解释当前 Trip | 只读问答中暗中写版本 |
| Commerce | 酒店/铁路/旅行商品查询和未来受控交易入口 | 使选中商品等同于预订成功 |

Validator / Repair / Reporter 属于门禁及渲染节点，**不另算 Agent**；餐饮、酒店、铁路、天气、预算优先为 Tool/Service/Provider Adapter。通用 Runtime、Router、Tool Governance、Prompt 版本、Memory 和 Trace 复用平台基础设施；MCP 是可选接入形态，不能因为 Provider 是 MCP 就自动认定数据实时/可信。

意图逻辑包括 `EXPLORE / PLAN / QUERY / MODIFY / RESTORE / COMMERCE_QUERY / OUT_OF_SCOPE`。这些是业务意图名，不要求直接替换主图现行枚举或 `route_mode`。

---

## 5. 五条权威业务主链

### 5.1 F-01：AI 创建 Trip

`首页/聊天输入 → Travel V2 规划 SSE → 需求抽取 → 必要追问 → Research → Planning → Optimization → Validator/Repair → 正式 Trip 创建（初始 Revision）→ 返回 Trip ID → GET 权威 Trip → 结构化工作台`

- 无明确目的地转探索；明确目的地与最低条件满足时直接规划。
- `TripBrief` 含 origin、destination、start_date（可空）、day_count、timezone、人数、预算口径、节奏、兴趣、保留与排除条件，字段须保留已明确值和必要来源。
- 未确定出行日期可以生成相对日程，但不能展示具体日期的实时天气、酒店房态或动车库存。
- 保存前进行版本适用的完整性校验；创建/保存失败不能说“正式行程已创建”。
- 若 SSE 在收到 trip_id 前断开，依赖原始幂等键/运行账本安全找回结果；查不到不意味着失败。

### 5.2 F-02：AI / 手动修改 Trip

`当前 Trip + expected_revision → 明确 MODIFY 意图/操作目标 → 受约束编辑方案 → 尽量局部优化 → Trip 统一 Validator → CAS + 幂等事务提交 → 新 Revision + Diff → GET 权威 Trip → 已保存`。

- AI、手动、选入 Provider 候选使用同一正式写入不变量；能使用类型化操作的优先使用已有结构化编辑 API。
- AI 对话可提出方案，但不能依靠模型直接写库；整文档替换也必须经过等效硬约束。
- 影响范围：单 POI→半天→当天→必要的跨天/全局，只有明确指令或无法局部满足且用户授权时才扩大。
- `INFO` 正常保存；`WARNING` 在不违反硬约束时保存且标注；`BLOCKED` 不保存，输出结构化冲突详情并挂起待澄清。
- 挂起对象的逻辑契约：`pending_id, trip_id, base_revision, conflict_codes, affected_item_ids, allowed_resolutions, expires_at`。可复用现有会话/检查点机制，不强制新表；恢复前再次校验版本与身份。
- 保存 UI 必须区分 `VALIDATING/SAVING/SAVED/INVALID/CONFLICT/FAILED/UNKNOWN_COMMIT/PENDING_RESOLUTION`；乐观预览不是正式行程。

### 5.3 F-03：酒店搜索 → 选为住宿

`独立或工作台查询 → Travel Query Service → Provider Adapter → 标准化酒店候选/Evidence → 用户选择并指定入住/退房 → Trip 写服务校验 → 新 Revision → 住宿卡`。

- 搜索只读，未选不保存。选为住宿仅代表计划参考，`not_booked`；不能冒充成交或可订房态。
- 保存酒店 ID/供应商、来源、查询时刻、入住日期范围、被验证的参考字段。
- 酒店 POI 信息存在但没有真实库存源时允许作为“住宿地点参考”，不得显示已核实可订房型或房态。
- 动态价格后续变化仅更新 LiveObservation，不改变用户 SelectionSnapshot 与 Revision。

### 5.4 F-04：铁路查询 → 选为去程/返程

`输入出发/目的地、日期 → Rail Query Service → Provider Adapter → 真实车次候选 → 用户选中 → 验证车站/日期/时刻/接驳 → Trip 写服务 → 新 Revision → 交通卡`。

- 未确定真实日期或 Provider 无可核验车次时不显示虚构班次。
- 列车安排记录来源、车次、车站、出发/到达时刻、日期与查询快照，`not_booked`。
- 选中交通后必须检查第一天抵达→活动、末日活动→车站、跨午夜和跨时区的时间约束。
- 不静默代替用户选择其他列车。列车时刻、价格变化作为独立动态提示。

### 5.5 F-05：撤销/恢复/再次恢复

`读取 revision 历史 → 用户明确目标版本 → 验证 expected_revision → 读取目标快照 → 完整性校验 → CAS + 幂等提交 → 生成新 Revision → 展示恢复摘要与 Diff`。

示例：R4 恢复 R2 内容产生 R5；再恢复 R4 内容产生 R6。历史 R2–R6 均保留。所谓 Redo 只是再次 Restore，不建立复杂前端历史指针栈。历史版本可恢复用户选择，但不可宣称其旧价格、车次状态和库存仍然有效，也不能撤销已发生的外部交易。

---

## 6. Provider 统一数据及“查询—选择—动态观测”

### 6.1 三种不可混淆的数据

| 模型职责 | 业务语义 | 是否写入正式 Revision |
|---|---|---|
| SearchResult | 当前只读候选，含可用证据 | 否 |
| SelectionSnapshot | 用户明确选入行程的酒店、餐厅、景点或车次身份及事实快照 | 是 |
| LiveObservation | 已选候选的后续动态查询结果（时刻、价格、库存等） | 否；仅更新展示与风险提示 |

这些是逻辑职责，未要求新建三张表。基于已有 `TripDocumentV2`、`arrangements`、`selection_id`、`MerchantSnapshotV2` 等复用。

### 6.2 字段真实性

Provider 结果逻辑契约包含：`provider_id, external_id, category, source, observed_at, valid_until?, verification/data_status, evidence_refs, identity/name, geo?, price?, currency?, inventory?, schedule?`。每个动态字段独立验证；无来源即未知，过期不能标实时。执行成功、业务有结果、事实有效、当前可订是不同状态。

### 6.3 数据源与降级矩阵

| 类型 | 基础可交付 | 有动态 Provider 才能展示 | 失败降级 |
|---|---|---|---|
| 景点/地图 | 已核实地址/坐标、概况、位置 | 当前开放、门票等 | 静态事实+来源，动态字段未知 |
| 餐饮 | POI/餐厅资料、位置 | 实时营业、评分/人均等可核实字段 | 只展示可信字段，允许稍后重试 |
| 酒店 | 酒店地点/住宿区域建议 | 房态、房型报价与取消政策 | 允许“住宿地点参考”，禁止可订承诺 |
| 铁路 | 路线的一般性建议 | 具体班次、日期、票价、余票 | 无实时源即不可用，不编造车次 |
| 天气 | 常识与已知背景 | 指定城市、日期预报 | unavailable/stale 如实说明 |
| 交通路线 | 有依据的距离/时长估算 | 实时路线和导航状态 | Estimated 或 Unknown；不默认 0 分钟 |

12306 MCP 是铁路候选 Adapter，需核实数据来源、服务条款、授权、时效与限制；MCP 本身不代表官方实时保障。

### 6.4 费用与时间缓冲

`required_gap = transit_duration + transfer_buffer + activity_buffer`。其中：
- Verified：可靠 Provider 耗时；
- Estimated：具备可用坐标时，可由直线距离、保守绕行系数、经校准速度与缓冲给出区间和 WARNING；不能固定用 `15km/h` 或 `45 min` 对所有城市/场景硬编码；
- Unknown：无法可靠估计时，不用 0 分钟排程；如涉及列车、营业结束等硬截止，可要求核实或阻止提交。

车站出站、去车站进站、酒店办理入住、景点间换乘采用不同可配置策略。缓冲是时间约束，不必自动创建伪造的活动项。旅行预算与模型 Token/Provider 成本分账。

---

## 7. 正式 Trip、Revision 和写入完整性契约

### 7.1 权威来源与数据职责

`Trip`：当前正式版本、归属、标题/生命周期；`TripBrief`：条件和时区；`TripDocument`：按天项目、路线、住宿和交通安排、健康风险；`Revision`：不可变快照、变更类型和摘要；`EditOperation`：提交操作幂等与归属；`Conversation`：AI 多轮和待澄清；`Share`：固定 Revision 的受控只读访问。

基于当前模型复用，不因逻辑对象单独建表。Checkpoint 是执行状态，不可替代正式 Trip 数据。用户切换 Trip 时必须拒绝旧 run 覆盖新 Trip。

### 7.2 单一写入门禁

所有正式写路径的最终提交都要满足：
1. 服务端验证的 `tenant_id/user_id` + 资源级授权；
2. 输入 Schema / 引用/日期一致性、事实可信限制；
3. 保留/固定项目的规则；
4. 同一套日程重叠、交通缓冲、可信营业时间等完整性硬约束；
5. `expected_revision` CAS，必要事务行锁；
6. 幂等键和相同请求内容重放；
7. Trip、Revision、操作账本的一致事务写入；
8. 确认提交后方能返回 `saved:true` 或发送“已保存”UI 事件。

覆盖创建、类型化编辑、`apply_document`、餐饮/酒店/铁路选择、模板复制、Restore。可保留各入口的特定 Validator，但所有入口执行同等核心硬约束。**不得单纯通过 HTTP 中间件校验而让内部 Service 绕过**。新增统一方法名应适应现有架构，不强制另建 `TripValidator.verify_integrity()` 类。

### 7.3 业务校验结果语义

- `INFO`：已知可行。
- `WARNING`：合法但紧凑或依赖不确定估算；可保存，保留证据与提示。
- `BLOCKED`：实际硬重叠、明确必去/固定项非法删改、已核实闭馆、关键交通衔接不可能或其他硬规则；禁止写入。
- `PENDING_RESOLUTION`：业务层待用户选择如何解决 BLOCKED；不视为已保存，也不改变正式 Revision。

被 BLOCKED 后的追问必须基于 Validator 的真实结构化冲突，不得凭模型幻觉给出虚构理由；可选动作必须真正可执行。

### 7.4 时间模型

按类型区分：
- `LocalDate`：酒店入住/退房、行程日期，如 `2026-10-15`；
- 相对日期/时间区块：未定日期的“第一天下午”；
- `LocalTime` + 适用日期 + IANA Timezone：已绑定日期的景点时间；
- `Instant` / RFC3339/ISO8601 带偏移：列车发/到站时刻、Provider 观测时刻等真实瞬间；
- `Duration`：停留/换乘分钟数。

可兼容保存 `{local_datetime,timezone_id,utc_instant}` 三元组，但三值必须表示同一个瞬间，由系统派生和校验，不允许独立更新；DST 重复/不存在时间必须消歧。跨时区的列车出发与到达分别关联车站当地时区。历史数据兼容优先，不能直接删除现有当地日期/时间字段。

### 7.5 Restore/分享区别

恢复创建**新** Revision；分享引用**旧的固定** Revision。恢复价格/班次选择不恢复其历史 live 可用状态。分享有独立权限/撤销/有效期策略，不泄露聊天、Trace、密钥和私密偏好。

---

## 8. SSE、REST、幂等与前端状态一致性

### 8.1 总原则

SSE 是真实过程和运行结果事件通道，REST 正式 Trip 是最终行程事实源。收到成功提交事件后可权威 GET 核对；断线时按现有 `(stream_id, seq)` 续传和去重语义处理，续传不可恢复时通过 REST/操作账本核对，不凭 `[DONE]` 判定写成功。

### 8.2 操作状态

`PENDING / RUNNING / PENDING_RESOLUTION / SUCCEEDED / FAILED / CANCELLED / UNKNOWN`（逻辑状态，实际字段须映射现有 Runtime）。操作已执行成功但网络响应丢失时，前端不能显示失败，也不能更换幂等键重复创建。

同一个逻辑写操作使用一个客户端 UUID/稳定标识，已有 V2 写接口请求头为 **`Idempotency-Key`**。前端请求封装已有生成器，但重试需复用同一键。现有 `edit_operations` 是**成功操作账本**，不是完整运行状态表。是否新增“按操作标识查询终态”的 API，待后端审计已有运行账本；如确实缺失，目标接口仅为提案，不能在前端当作现行 API 调用。

### 8.3 读回核对

- 当前已知权威 GET：`GET /api/travel/v2/trips/{trip_id}`。
- `expected_revision` 属于写操作 CAS，不应强行加到 GET 查询最新版本的参数里。
- 得到更高的 Revision 不代表**本次操作**已经成功，还需要对账操作归属/幂等记录。
- 创建 Trip 时丢失返回的 trip_id，需要通过原始请求标识和现有幂等创建机制恢复；不能仅靠“GET 最新 Trip 列表”猜测是哪一笔。
- 允许有限轮询与明确退出/超时状态；不得永远转圈或无限重试。
- 保存失败、版本冲突和后台终态不明有各自独立 UI 状态。

### 8.4 运行中切换与取消

每个事件关联 run、trip、revision/版本预期；拒绝迟到旧 run 覆盖较新 run 或不同 Trip。用户取消只读搜索/规划后保留真实已完成步骤；取消后迟到结果不得写入正式行程。交易类操作不能通过取消绕过确认规则。

---

## 9. 当前仓库基线（截至本次 GitHub main 静态查看）

> **重要**：以下为 `main` 快照中发现的入口，不代表另一个会话/工作树最新后端代码已经合并，不代表运行验收通过。施工前要重新取当前 HEAD 和最新后端交付 SHA。

| 现有位置 | 事实/使用方式 |
|---|---|
| `backend/travel/register.py`, `backend/travel/graph_builder.py` | 旅游域图、真实节点拓扑仍需针对最新实现审计 |
| `backend/app/api/routes/travel.py` | 旅游主规划/SSE 相关入口 |
| `backend/travel_v2/api/trips.py` | V2 Trip CRUD、Revision 历史、文档替换、结构化编辑、餐饮/住宿/铁路选入、Restore |
| `backend/travel_v2/api/search.py` | 景点、餐饮、酒店、铁路只读查询 |
| `backend/travel_v2/services/trip_service.py` | 正式 Trip 创建/读取、首次 Revision、创建幂等 |
| `backend/travel_v2/services/trip_edit_service.py` | 编辑、选择、CAS、事务记录及校验 |
| `backend/travel_v2/services/revision_service.py` | 历史和 Restore |
| `backend/travel_v2/repositories/revision_repository.py` | 不可变快照含 `change_summary/change_type` |
| `backend/travel_v2/repositories/edit_operation_repository.py` | 仅成功编辑操作账本 |
| `backend/travel_v2/models/trip.py` | TripBrief IANA 时区、酒店 LocalDate、铁路本地时钟值、健康标记 |
| `frontend/src/api/travelV2.ts` | 已包含 Trip、模板、查询、选择、Restore 和 idempotency 逻辑封装 |
| `frontend/src/components/travel/v2/` | 已存在 Home、Trip 工作台、聊天、地图、搜索、编辑、时间轴等组件 |
| `frontend/src/app/travel/` | 已存在首页、聊天、行程列表/详情、模板路由 |

**P0 静态审计候选**：`apply_operation()` 可见调用 `_validate_edit_schedule()`，`apply_document()` 的可见实现未发现相同调用；由于文档替换/AI 和选择路径可能走此入口，应由后端会话检查是否存在硬约束不一致。先核对真实调用链与反例测试，未证实前不直接宣称线上漏洞。

**已存在铁路 60 分钟约束**：模型中目前对去程到站和返程发车有硬编码 60 分钟缓冲；设计目标是可配置且场景化的缓冲，不应在没有审计前推倒现有校验。

**Provider 现状提示**：已见高德商户 POI 与 12306 来源身份的选择约束；仍须核查真实 Provider 的可用性、时效、授权与响应真实性，不能从代码路径存在推导“全链路 live 已通过”。

---

## 10. 确认过的 API 与待定接口矩阵

| 逻辑能力 | 已发现的当前 API | 性质与约束 |
|---|---|---|
| 查询 Trip | `GET /api/travel/v2/trips/{trip_id}` | 正式版本事实源；按身份/租户授权 |
| Trip 列表 | `GET /api/travel/v2/trips` | 私有列表 |
| 创建 Trip | `POST /api/travel/v2/trips` | 幂等写入，Revision 1 |
| 结构化编辑 | `POST /api/travel/v2/trips/{trip_id}/edits` | 类型化操作、CAS、幂等 |
| Trip 绑定 AI 修改 | `POST /api/travel/v2/trips/{trip_id}/ai-edits` | 请求含 `expected_revision/user_message`、可选 `conversation_id`；身份来自服务端；同键原始请求持久占位；只提交一个白名单结构化操作并复用统一 Validator/CAS/Revision。成功回执含 `base_revision/new_revision/operation_key/change_summary/document/diff`；只读、澄清、不支持和 UNKNOWN 不产生正式 Revision。 |
| 整文档替换 | `PUT /api/travel/v2/trips/{trip_id}/document` | AI/整文档适配，须统一硬校验 |
| Revision 历史 | `GET /api/travel/v2/trips/{trip_id}/revisions` | 含 `change_summary` |
| Restore | `POST /api/travel/v2/trips/{trip_id}/restore` | 新 Revision |
| 餐饮选入 | `POST /api/travel/v2/trips/{trip_id}/selections/meals` | 参考地点加入 Trip |
| 酒店选入 | `POST /api/travel/v2/trips/{trip_id}/selections/lodgings` | 未预订住宿参考 |
| 动车选入 | `POST /api/travel/v2/trips/{trip_id}/selections/intercity-trains` | 未购票交通参考 |
| 地点搜索 | `POST /api/travel/v2/search/places` | 只读 |
| 美食搜索 | `POST /api/travel/v2/search/food` | 只读 |
| 酒店搜索 | `POST /api/travel/v2/search/hotels` | 只读 |
| 铁路搜索 | `POST /api/travel/v2/search/trains` | 只读 |
| V2 SSE 规划 | `POST /api/travel/v2/plan/stream`（仓库域文档） | 复用域图；需审计保存终态 |
| 模板 | `/api/travel/v2/templates...`（详见当前路由） | 复用现有查询/复制 |
| 分享/导出 | **未在本次上述 API 清单中确认** | 前端可先做界面，但真实服务动作待合同确认 |
| 查询 AI 操作终态 | `GET /api/travel/v2/trips/{trip_id}/ai-edits/{operation_key}` | 只读；身份、tenant、owner、Trip 和 key 联合限域；成功必须关联真实 `edit_operations`；UNKNOWN 不从 Revision 变化推断 |
| 查询冲突待确认项 | `GET /api/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}` | 返回持久化冲突、允许选项和过期状态；pending 期间正式 Revision 不变 |
| 确认冲突解决 | `POST /api/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}/resolve` | Body 只接收服务端提供的 `resolution_id`；使用独立 `Idempotency-Key`，重新校验后复用 CAS/Revision/操作账本 |
| 取消/拒绝冲突解决 | `POST /api/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}/cancel` | 与确认共用 Trip 行锁；终态 pending 不可再次执行 |

> BE-4C 的 `ai_edit_requests` 账本同时持久化原始 AI 请求和 pending 生命周期，没有新增 Trip 版本系统或 pending 表。可安全给出下一可用时刻的单项时间冲突才提供受限解决选项；锁定、固定、必去、闭馆等无法安全自动修复的冲突保持 BLOCKED。确认使用独立幂等键，并在同一事务内写正式 Trip、Revision、`edit_operations` 与 AI 回执；失败、取消和过期不写正式 Trip。90 秒陈旧 `processing` 查询返回 UNKNOWN，仍可能在原工作运行中，不代表取消或回滚；原调用迟到时只允许同一请求账本身份、作用域和幂等键安全保存 Pending，不重新调用模型，也不覆盖其他终态。复杂多操作、整体重规划和无法从当前 Trip 快照确认的地点修改会澄清或拒绝。

> 设计到实现的映射原则：`REUSE / FIX / BUILD / BLOCKED`；任何新增 URL、字段或表必须在 B0 差距报告中说明证据、调用方及迁移方案。

---

## 11. 前端先行专项计划（允许现在启动）

### 11.1 前后端并行规则

- **前端 Codex 只在专用前端分支/Worktree**；不修改 `backend/**`、迁移、核心 Runtime 或其他后端会话任务文件。
- 正式产品设计以本稿为目标，现有前端 TS 类型和已确认后端 API 为施工事实；存在冲突时报差距，不发明“已存在”的服务接口。
- 不重构全部旅游页面。先复用 `frontend/src/components/travel/v2/`、`frontend/src/api/travelV2.ts`、`frontend/src/app/travel/`。
- 真实数据默认经既有 API；测试用 fixture/mock 限于测试、Storybook/明确 demo 入口，不能在正式页面静默兜底虚假酒店/动车/POI 数据。
- UI 完成和业务联调完成是不同验收状态；未接真实后端的动作标 `UI_READY / INTEGRATION_PENDING`，不能标 `PASS_E2E`。
- 对已有自动保存、编辑、撤销、搜索能力优先审计和修复 UX 细节，不再造平行全局状态容器、第二个 Trip API client 或旧版 Draft/Apply。

### 11.2 推荐前端 Batch

| Batch | 可以现在做 | 对后端依赖 | 本批完成条件 |
|---|---|---|---|
| FE-0 现状/接口审计 | 画现有路由、组件、状态/请求图；与稿件逐项标 REUSE/FIX/BUILD/BLOCKED | 无 | 审计报告和文件列表；**不改业务代码** |
| FE-1 导航与布局收口 | 首页/工作台/模板/我的行程；PC 三栏，移动端单列与返回聊天；减轻视觉噪声 | 使用现行 API | 路由正确、无死按钮、手机可读可点、无 V1 应用草案 |
| FE-2 统一行程视图与编辑反馈 | 时间轴、酒店/交通卡职责分离、保存/冲突/错误/历史 UI、可用 Undo 控件 | 当前 Trip/Revision/编辑 API | 真实已有 API 路径的定向集成通过；未知新状态用契约测试，不伪造成功 |
| FE-3 查询/Provider UI | 独立查询 + 工作台 Tab 共用 `TravelSearchPanel`/API、证据/时效、空/错/部分成功、选择目标 Trip/Day | 现有搜索/选择 API | 查询纯只读、选择动作状态完整、数据来源显示准确 |
| FE-4 预留新增能力 UI | BLOCKED 待澄清面板、分享固定版本界面/导出入口、断流/未知提交提示 | 若 API 未稳定仅接类型化 facade/fixture | 组件测试通过；未接 API 的真实动作保持禁用并注明待联调 |
| INT-1 后端交付后联调 | 新后端 DTO/状态映射、真实运行状态查询、SSE 恢复、完整 Validator 反馈、分享/导出 API | **必须等待后端提供 Commit + Schema** | 真正端到端验收通过，禁用/假状态全部移除或明确标示 |

### 11.3 前端适配层要求

- 单一 `TravelV2Api` 或复用现有 `travelV2.ts` 作为后端交互适配，不在每个 UI 组件到处拼 URL。
- 从真实 `Trip` 结果派生 `TripViewModel`；以 `trip_id/revision` 标记缓存身份；保留最新写操作 ID。
- 统一 mutation 状态：`idle|validating|saving|saved|pending_resolution|conflict|failed|unknown`。组件不可自己宣布 `saved`。
- Provider UI 统一显示 `source/status/observed_at`，无字段则隐藏或显示“未知”；Mock Fixture 必须可识别。
- 后端未提供的新字段允许单独定义 `proposed` TypeScript 契约，但不得伪装现有 API 的真实返回值。
- SSE 只展示服务端真实事件；前端可以模拟组件状态做测试，但正式产品不做假进度或固定计时器。
- 与当前前端测试布局和 API 客户端的兼容性优先；不扩展无关依赖。

### 11.4 前端可独立交付的验收

| ID | 前端先行验收 | 允许的证明 |
|---|---|---|
| FE-P0-01 | 首页/模板/行程列表/详情导航可用 | 浏览器截图+真实现有路由 |
| FE-P0-02 | PC 三栏和手机独立单列状态 | Playwright 手机/桌面视口 |
| FE-P0-03 | 行程 Tab 与地图选择互不污染 | 组件/浏览器交互，真实地点数据优先 |
| FE-P0-04 | 保存状态不在响应前宣称成功 | 延迟/失败/版本冲突的组件及 API 测试 |
| FE-P0-05 | 删除、拖动、替换等不绕过后端写服务 | 网络请求/状态断言 |
| FE-P0-06 | 查询无 Trip 时只读；有 Trip 才可选择写入 | 前端网络与 Revision 断言 |
| FE-P0-07 | Provider 数据无来源时不显示“实时” | 空/失败/过期 fixture 测试 |
| FE-P0-08 | 切 Trip 后旧 run/result 不污染当前页 | 竞争条件反例测试 |
| FE-P0-09 | UI 界面没有“应用草案”旧机制 | 真实路由截图及查找 |
| FE-P0-10 | 已实现接口使用现有 `travelV2.ts` 而非重复 API 层 | diff/调用关系审查 |
| FE-P0-11 | Share/新终态 API 尚未完成时不会虚假宣称已分享/已保存 | 明确禁用及 `INTEGRATION_PENDING` 报告 |

**未接后端不能作为真实业务验收**：完整 AI 修改自动保存、真实 Provider 购买相关信息、分享链接权限、失联后操作状态查证，均须 INT-1 通过。

---

## 12. 后端交付后的联调对账要求

另一后端会话每批至少提供 `branch/commit SHA/是否合并`、OpenAPI 或真实 Schema、变更接口、服务调用链、迁移说明、成功与失败例、定向测试命令和结果、阻断项。前端 Codex 不得仅依据聊天文字更改 API 契约。

重点 P0 对账：
1. 所有 Trip 写入入口执行同等硬约束（特别是 `apply_document` vs `apply_operation`）。
2. BLOCKED 冲突具备结构化详情并可继续多轮选择，正式 Revision 在挂起时不变。
3. 接驳未知/估算/已核实状态、缓冲策略及跨午夜跨时区校验。
4. SSE `done`、正式 Revision、成功操作账本、前端提示真实一致；断流不会重复提交。
5. `Idempotency-Key` 在前端跨重试保持不变；操作无法定位时显示 UNKNOWN 而非失败/成功。
6. Provider 两种查询入口返回同一规范化模型；已选快照不被后续动态报价静默覆盖。
7. 分享固定 Revision 与只读授权，不泄露他人数据。

---

## 13. 完整 P0 / P1 验收口径

**状态规范**：`PASS`（具有证据） / `FAIL`（反例失败） / `BLOCKED`（明确外部依赖） / `NOT_RUN`（未测） / `UI_READY`（仅 UI 可用）。HTTP 200、页面可渲染、Mock 截图不等于业务 PASS。

### 13.1 P0 用户价值路径

| ID | 场景 | 通过标准 |
|---|---|---|
| P0-01 | 模糊探索 | 未选目的地不假建 Trip；用户选城市后继承需求 |
| P0-02 | 一句话规划 | 出发地/目的地/日期/人数/约束正确，不误抽取 |
| P0-03 | 多轮追问 | 不重问已知条件，歧义明确后继续 |
| P0-04 | 创建正式 Trip | 数据库 Revision 1 与工作台相符，异常无假成功 |
| P0-05 | 只读问答 | 无修改指令时 Revision 不变 |
| P0-06 | AI 局部修改 | 目标范围内变化，未授权其他日/固定项不变 |
| P0-07 | 硬冲突 BLOCKED | 提供真实冲突与澄清选项，正式 Revision 不变 |
| P0-08 | WARNING 柔性保存 | 仅软风险可保存并显示 WARNING；硬重叠不放行 |
| P0-09 | 手动编辑 | 校验通过自动保存，刷新不丢 |
| P0-10 | 所有写入口完整性 | AI 整文档/结构化/选入/模板/Restore 无绕过 |
| P0-11 | Undo/Redo | 创建新 Revision，历史内容和摘要可追溯 |
| P0-12 | 幂等/CAS/并发 | 同键重试无重复，跨标签旧版本不能覆盖 |
| P0-13 | 查询只读 | 美食/酒店/铁路/地点查询不修改 Trip |
| P0-14 | 餐饮选入 | 指定日和午晚餐、来源、Revision 正确 |
| P0-15 | 酒店选入 | 入住范围正确，`not_booked`，无假房态 |
| P0-16 | 铁路选入 | 车站/日期/时刻准确，`not_booked`，接驳合理 |
| P0-17 | 地图和路线 | 坐标/顺序一致，未知时不绘假实时路线 |
| P0-18 | Provider 失败语义 | 超时/空/不可用/过期/部分成功区分，动态事实不造假 |
| P0-19 | SSE、REST 最终对账 | 断流后不显示虚假已保存，操作归属可核实或 UNKNOWN |
| P0-20 | PC/手机关键链 | 首页→规划→编辑→查询→返回→撤销 可用 |
| P0-21 | 模板 | 模板预览只读，创建独立 Trip |
| P0-22 | 分享固定 Revision | 后续修改不改变分享；跨租户安全；可撤销 |
| P0-23 | 导出 | 以指定 Revision 生成；ICS/PDF/图片按批次标注实际完成度 |
| P0-24 | 身份和租户 | 列表/详情/历史/分享/操作查询不存在横向越权 |
| P0-25 | 时间和时区 | 本地日期、跨午夜/跨区列车、DST 不错乱；历史兼容 |
| P0-26 | Trace 归因 | run/tool/provider、prompt、耗时、token 成本与正式终态可关联 |

### 13.2 P1 优化而非放行替代

- 局部 Diff 视觉表现、复杂跨日优化、移动端微动效、地图体验、丰富导出排版、Provider 比对缓存、更多候选城市与个性化偏好；不允许以完成 P1 替代未通过 P0。

### 13.3 测试分级与证据

遵守 `docs/development/testing-guide.md`：T0 静态/类型；T1 定向单元/组件；T2 跨模块、权限、事件与共享契约；T3 发布级 E2E。按风险使用最小充分集合，不为每次小改默认全量长回归。涉及正式写入、权限、租户、CAS、SSE、Schema 的改动至少进行所需 T2；最终放行需真实网关、浏览器和持久化证据。测试执行记录命令、通过/失败/跳过、耗时、未测项和原因。环境不支持的外部 Provider 测试标 BLOCKED，不能扩大 Mock 降低门禁。

---

## 14. 设计、施工、发布和 GitHub 规范

### 14.1 设计文档发布建议

- 本稿以独立 docs 任务分支、PR 形式发布到 `docs/domains/travel-v2/product-design.md`，供前端 FE-0 按明确设计版本审计；不在正在施工的后端工作树直接创建/修改该文件。
- 更新既有 `docs/domains/travel.md` 的导航链接即可，不复制完整正文；`docs/README.md` 如有必要增加入口。
- 文档合并后记录明确 Commit SHA；前端与后端在施工报告中引用同一 SHA/版本号，避免依据两个不同版本的设计稿。
- **文档合入 `main` 仅表示产品设计评审基线，不代表后端代码已完成、接口已冻结或通过 P0 验收**；保持 `v1.0-RC1` 标识，后端差距收口后再升级正式版本。

### 14.2 双会话/双 Codex 并行规则

- 后端任务按已约定分支进行，不因本稿重做。
- 前端只改 `frontend/**` 和该前端任务必须的测试；其任务范围由 FE-0 审计结论决定。
- 不共用正在使用的工作树，不 cherry-pick 未验证后端临时提交，不在 main 上工作。
- 遇后端 DTO 缺失：优先提交 API 契约问题与 `INTEGRATION_PENDING`，不临时创造相反语义的假字段或新版本账本。
- FE 每个 Batch 有独立测试、截图、文件清单、阻断清单和 PR；未验收不启动依赖批次。
- 用户确认后可直接在本机进行新页面集成，不要求灰度/影子双跑；涉及数据库迁移必须考虑回滚和持久数据安全。

### 14.3 最终交付产物

**唯一产品真源**为本 `product-design.md`。后续可以新增不重复其正文的执行型文件：
- `implementation-plan.md`：代码映射、FE/BE/INT 批次、owner、依赖、每批 commit 及状态。
- `acceptance.md`：验收清单执行记录、证据路径、通过/失败/阻断、复验结果。

三份文档用途不同，避免在多个地方反复维护同一个业务规则。

---

## 附录 A. 先行前端 Codex 任务说明（只允许 FE-0，再审批 FE-1）

> 请在当前仓库**另建独立前端 worktree/分支**。本任务依据《旅游 AI Agent 平台 V2｜统一产品与系统总设计书 v1.0-RC1》，但**当前只执行 FE-0 审计，未经确认不进入 FE-1**。
>
> 1. 读取 `AGENTS.md`、`docs/README.md`、`docs/domains/travel.md`、`docs/development/testing-guide.md`、当前 V2 前端路由与组件、`frontend/src/api/travelV2.ts`。核对当前 HEAD、未合并旅游前端任务以及后端另一会话正在开发的分支，不修改其工作树。
> 2. 对首页、聊天、Trip 工作台、时间轴、地图、餐饮/酒店/铁路搜索、编辑、撤销、模板、分享/导出、移动端状态逐一给出 `REUSE/FIX/BUILD/BLOCKED`，并写出文件路径和代码行号。
> 3. 审计前端是否仍存在旧“应用草案”、静态演示误入真实 UI、虚假“已保存”、跨 Trip 旧结果覆盖、查询误写入、断流后依赖 SSE `done` 判断已提交等问题，提供最小复现证据。
> 4. 根据现有后端已确认 API，列出 FE-1 至 FE-4 的具体文件级修改和依赖；未知后端状态字段只写提案，不擅自新增 API 或更新后端。
> 5. **本批零业务代码改动**，只交付审计与分批实施计划。报告必须包含分支/HEAD、事实证据、可先做项、后端阻断项、测试建议、待产品决策；未获确认停止。

## 附录 B. 待最终对账事项（不得默默推断）

1. 另一个后端会话最新 Commit/PR 与 main 的差异；是否已收口全部正式写入口校验。
2. AI 修改 `pending_resolution` 的实际持久/跨轮方案，以及前端响应 Schema。
3. SSE 与写操作的跨断线操作状态查证方式；是否需要新增受控查询 API。
4. 交通路线估算与时间缓冲、跨午夜/跨时区以及缓存事实状态的实现边界。
5. 分享权限、匿名访问、撤销/过期与 PDF/图片导出后端能力是否存在。
6. 真实酒店/铁路 Provider 的数据来源、授权、可用时效以及实际可公开的产品字段。
7. 旧 V2 前端并行改动/分支是否存在尚未合并的成果；不得直接覆盖。

**设计文档最终结论**：产品范围与关键业务语义已经足以启动隔离的前端 FE-0 审计和随后的不依赖后端变更的 UI 工作；**未授权启动后端重构、未获得真实 Provider 全链路验收，也不能将本稿视作“平台已完成”。**
