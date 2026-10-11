# Travel V2 前端 FE-1～FE-4 施工计划（FE-0 提案）

> 本文是 FE-0 审计产生的执行提案，不授权启动 FE-1。所有批次均以独立任务分支实施；先由评审确认批次和并行工作合并顺序。V2 正式行程始终以服务端成功校验并提交的 Trip + Revision 为准；不引入“应用草案”。

## 批次总览

| 批次 | 目标 | 依赖 | 交付边界 |
|---|---|---|---|
| FE-1 | 导航/目录、跨 Trip 状态隔离、首页与工作台响应式收口 | 当前已确认 V2 只读/现有写 API；无需新增 API | 能从首页到聊天、模板、行程和发现；失败/切换不暴露旧 Trip。 |
| FE-2 | 正式编辑状态、自动保存/未知结果、Revision 历史/恢复与撤销 | 已确认的编辑/restore/revision API；冲突及幂等语义须与服务端确认 | 手动编辑只有校验+事务成功后显示保存；冲突不写；未知结果不伪报失败。 |
| FE-3 | 地点/美食/酒店/动车搜索、地图可信标识和行程联动 | Provider 来源、有效期、验证级别、错误态及选择 API Schema 冻结；真实 Provider 可用 | 查询只读；用户明确选择才写 Revision；没有真实 Provider 的 live 验收不得通过。 |
| FE-4 | 工作台 AI 修改/冲突解决、固定 Revision 分享与导出状态 | AI 修改/`pending_resolution`/操作查证、分享权限及固定 Revision、导出格式/API Schema 与后端 Commit | AI 修改沿用正式验证写入口；分享固定快照；未具备后端能力时明确不可用/阻断，不使用 mock 成功。 |

## FE-1：导航、页面连通和状态隔离

### 具体文件

- 修改 `frontend/src/components/travel/v2/TravelV2Frame.tsx`：补齐移动端“发现”入口；导航、当前页面状态及访问路径统一。
- 修改 `frontend/src/components/travel/v2/TravelHome.tsx`：修正“浏览全部”，保留前 6 个首页推荐卡；复用现有模板和 V2 搜索数据。
- 新增 `frontend/src/app/travel/templates/page.tsx` 和模板目录组件（若目录组件适合放在 `components/travel/v2/`）：使用 `fetchTravelTemplatesV2`，不新增后端 API。
- 修改 `frontend/src/components/travel/v2/TravelV2TripWorkspace.tsx`：route `tripId` 切换时立即清除/隔离旧 Trip、旧错误、选中项、undo Revision 和旧请求结果；新 Trip 失败必须展示错误，不得保留旧行程可编辑。
- 检查 `frontend/src/app/travel/chat/page.tsx`、`TravelChatPage.tsx`、`TravelHome.tsx`、`TravelV2TripWorkspace.tsx`：明确“新规划→成功创建 Trip→工作台”与从既有 Trip 继续聊天的入口。只复用现有聊天/SSE 路径，不接 AI 修改写入。
- 视验收结果调整 `frontend/src/app/travel/itineraries/[id]/page.tsx`、`edit/page.tsx`、`layout.tsx`：保持演示 id 路由与正式 V2 Trip 边界清楚，不把 Demo context 的本地状态带入正式编辑。

### 复用方案

- 保留 `TravelHome` 的最近行程、模板卡、快捷搜索和 API 载入逻辑。
- 保留 `TravelTemplatePreview` 的只读预览与“复制为新 Trip”流程。
- 保留 `TravelV2Frame` 的桌面导航、移动端导航容器及 `MobileAssistantDrawer`，扩展现有项而不重造 shell。
- 保留 `TravelChatPage` 作为规划会话页；正式 Trip 仍由 REST 响应确认后打开。

### 验收

**P0**

- 新建或打开 Trip A 后，在同一客户端导航到 Trip B；B 成功时只显示 B，B 404/网络失败时显示错误页，绝不显示 A 的内容或允许编辑 A。
- 多次快速切换 Trip 时，旧 GET/revision 请求的迟到响应不能覆盖当前 Trip。
- 手机导航覆盖首页、行程、发现、我的；模板“浏览全部”到模板目录，复制仍创建独立 Trip。
- SSE `done` 但没有 REST 保存成功时，不显示“已自动保存”。

**P1**

- 桌面/手机入口可访问规划聊天、模板详情、我的行程、服务发现；返回导航保留正确 Trip/会话上下文。
- 无模板/模板 API 错误、无最近行程和加载中状态可读且不跳转错误路由。

### 浏览器实机场景

- Chrome 桌面：`/travel` → “浏览全部” → 模板预览 → 复制 → 新 Trip 工作台 → 返回。
- 桌面路由切换：A→B，分别模拟 B 成功、404、网络慢且 A 请求迟到；确认页面与当前 URL 一致。
- 手机视口 390×844：导航四项、目录滚动、模板预览返回、工作台切换、底部安全区/抽屉滚动。
- 聊天断网/服务端保存失败：页面不得显示保存成功，恢复网络后的路径不产生重复 Trip。

### 测试建议

- 新增 `TravelV2TripWorkspace.test.tsx`：route 切换、迟到响应、B 失败不泄漏 A、加载/空/错误状态。
- 新增模板目录页面测试：空/错误/目录卡和复制路由。
- 调整 `TravelHome`/`page.test.tsx`：浏览全部链接到目录路由。
- 扩充 `TravelChatPage.test.tsx`：SSE done + REST 失败/响应超时，不产生成功标签；安全重试沿用逻辑操作键（若现有 API 能支持）。

## FE-2：正式编辑、保存结果与 Revision 历史

### 具体文件

- 修改 `frontend/src/components/travel/v2/TravelV2TripWorkspace.tsx`：编辑状态、冲突/未知结果提示、历史/恢复入口和完整刷新状态。
- 修改 `frontend/src/components/travel/v2/TravelV2EditDialog.tsx`：校验期间禁用重复提交；按明确的响应/错误类别呈现状态；服务端失败时保持原 Revision。
- 修改 `frontend/src/api/travelV2.ts`：仅在后端已确认 schema 的范围内暴露现有 revisions/restore/edits 方法；为一次逻辑操作保留同一 `Idempotency-Key`。禁止在本批猜新 endpoint。
- 新增 `frontend/src/components/travel/v2/TravelTripHistoryPanel.tsx`（桌面侧栏/手机抽屉共用）：Revision 列表、摘要、时间与恢复确认；必要 Diff 使用已有快照。
- 修改 `frontend/src/components/travel/v2/travelV2PlannerAdapter.ts`：去掉固定 `Asia/Shanghai` 和 children=0 的失真映射；未知时长保留 unknown/unavailable，不写成 0；按确认 Schema 映射人数及时区。
- 修改 `frontend/src/components/travel/v2/TravelSearchPanel.tsx`：选入失败/超时结果用与普通编辑一致的 UNKNOWN/冲突语义。

### 复用方案

- 继续调用 `applyTravelTripEditV2`、`restoreTravelTripV2`、`fetchTravelTripRevisionsV2`，不另建状态账本。
- 继续用 `TravelV2EditDialog`、现有 `ApiError` 与 revision CAS；restore 产生新 Revision，不做客户端回滚。
- 所有正式写入口（创建/模板复制/整文档保存/结构化编辑/选入/恢复）统一依服务端 validator；前端不能以本地计算替代校验。

### 验收

**P0**

- 校验或 CAS 冲突：正式 Revision 不变；页面显示冲突/刷新动作；用户重看最新 Trip 后明确重试。
- 成功写入：只有收到服务端成功响应后更新正式内容、Revision 和“已保存”；每次逻辑操作重试使用同一键和相同 payload。
- 超时/断流：结果未知，不宣称“原行程保持不变”；按现有确定性接口核对 Revision/操作结果；如果后端没有查证能力则保持 BLOCKED，不能伪造恢复成功。
- 时区、儿童人数和未知接驳耗时映射不丢失；未知不显示为 0 分钟或保证可行。

**P1**

- Revision 历史列表可以从任意一版 Restore 到目标快照，Restore 返回新的 Revision；Undo/Redo 不在前端维护影子栈。
- 查询/聊天问答不产生 Revision；每种明确写操作只产生符合服务端契约的 Revision。
- 历史加载失败可提示“历史暂不可用”，不误报 Trip 本身未保存。

### 浏览器实机场景

- 编辑地点/时间/排序/天数；检查保存中、成功新 Revision、刷新后数据仍在。
- 两个标签页同时编辑：一个成功，另一个触发 CAS 冲突后刷新；旧 payload 不覆盖新版本。
- DevTools 离线/延迟模拟请求成功后丢响应；确认 UNKNOWN、同键核对/重试，没有第二次重复写。
- 历史恢复到旧快照后生成新版本；手机历史抽屉和桌面历史面板行为一致。

### 测试建议

- 新增 `TravelV2TripWorkspace.test.tsx` 保存状态、409、传输未知、历史恢复、Revision 更新覆盖。
- 扩充 `travelV2.test.ts`：同一 operation retry key 稳定、payload 不变；restore 按既定 schema。
- 扩充 `travelV2PlannerAdapter.test.ts`：非上海时区、成人/儿童拆分、未知交通耗时。
- 扩充 `TravelV2EditDialog.test.tsx`：冲突/未知错误不关闭、不提示成功。

## FE-3：搜索、真实 Provider 与地图事实状态

### 具体文件

- 修改 `frontend/src/components/travel/v2/TravelSearchPanel.tsx`：仅用响应 `source/status/queried_at/disclosure`，去除请求中硬编码 Provider 为最终事实；处理旧查询迟到、取消/切换、未知提交和结果有效状态。
- 修改 `TravelSearchActivity.tsx`：复用并呈现查询/Provider 终态，保证历史事件不混淆当前 Trip。
- 修改 `TravelV2Map.tsx`：按 `verification` 标注/过滤 marker，空状态不固定展示杭州，明确坐标可信级别；只绘制后端明确提供的路线数据。
- 修改 `travelV2PlannerAdapter.ts` 与 `travelV2.ts` 类型：仅按后端冻结 Schema 表达 result freshness/verification/availability；不是现在新增 API 的授权。
- 修改 `TravelSearchPanel` 选择映射：确保用户明确选择结果后才写 Selection Snapshot，查询本身只读。

### 后端/Provider 前置条件

- 每种查询返回真实来源、查询时间、有效期/过期状态、验证级别、失败类别；Seed/Mock 明确可识别，不得报 live。
- 服务端对所有选入写路径做权限、选择 ID、CAS、完整 validator、事务与幂等校验。
- 需要有真实 Provider 凭据/连通、限流、超时和降级观测；无凭据或 Provider 不可用时验收状态应是 BLOCKED/不可用，而不是假数据成功。
- 地图路线几何、通勤耗时及置信度如未在契约里提供，前端只显示地点点位和已知信息，不自行造路网结果。

### 验收

**P0**

- 无 Trip 查询不写入 Trip/Revision；有 Trip 查询仍只读，只有明确加入/选入才产生正式写入。
- Provider 返回 success/no_results/business_failure/timeout/unavailable/disabled 等状态均如实显示；source 与查询时间只来自真实响应。
- 酒店未知房态/价格、餐馆未知营业事实、动车未知余票/延迟清晰标为未知；未购买状态不暗示预订完成。
- 旧 Trip 查询慢响应晚到不能覆盖当前 Trip 的结果或位置。
- 地图不将 estimated/unknown 坐标包装成验证地点，地图空状态不引导到错误城市。

**P1**

- 查询结果与地图 marker/时间轴选择联动；加入后服务端快照与 Revision 同步刷新。
- mobile 列表→详情→明确加入的按钮、返回位置和滚动可用；Provider 错误不会清空其他行程数据。

### 浏览器实机场景

- 无 Trip 搜索酒店/美食/动车：结果仅展示不修改任何正式 Trip。
- 有 Trip 分别选择/不选择结果，检查 Revision 增量、保存状态、`not_booked` 标识。
- Provider 成功、空结果、禁用、超时、真实凭据缺失；检查 source/time/未知字段和错误文案。
- 手机窄屏查询列表、详情抽屉、地图点位；快速切换城市/Trip 并让上一个查询迟到。

### 测试建议

- 新增 `TravelSearchPanel.test.tsx`：查询只读、明确选择才写、迟到响应隔离、source/status 渲染。
- 新增 `TravelV2Map.test.tsx` 或提取可测纯映射：verification marker、无坐标、空状态城市、选择联动。
- 扩充 API contract tests 覆盖 Provider `source/status/queried_at/disclosure` 与未知值。
- 后端真实 Provider E2E 和数据证据由后端/集成批负责；纯前端 Fixture 测试不能标记 live 验收通过。

## FE-4：AI 工作台修改、冲突解决、分享与导出

### 具体文件

- 修改 `frontend/src/components/travel/v2/TravelV2TripWorkspace.tsx`：连接工作台 AI 会话入口；呈现请求进度、结构化变更、服务端校验回执、冲突/UNKNOWN 状态。
- 新增/调整 `frontend/src/components/travel/v2/TravelV2TripAssistant.tsx`、`travelV2Assistant.ts`：若并行后端工作最终合入，先 review 并复用其中已有实现；本报告基线未包含这些文件，禁止重复造轮子。
- 新增 `TravelPendingResolutionPanel.tsx`：只按后端最终的结构化 pending-resolution Schema 呈现选项；用户明确选择后重提并重新服务端校验。不得本地合成“冲突已解决”。
- 新增 `TravelShareDialog.tsx`、`TravelExportMenu.tsx`、`frontend/src/app/travel/share/[shareId]/page.tsx`（路径/名称以最终 contract 为准）：固定 revision 分享只读页和导出入口。
- 修改 `frontend/src/api/travelV2.ts`：仅在后端 Commit+Schema 合入后增加正式 API adapter；不得在 FE-0/FE-1 预先猜 URL、token、权限或导出格式。

### 必须先解的后端阻断

- AI 编辑请求和返回结构、pending-resolution 持久/跨轮模型、冲突等级及候选操作。
- SSE 与 REST 写操作断线后如何核对最终状态/幂等操作结果；查询能力若缺失必须由后端提出并实现，FE 不自行新增 API。
- 创建分享时固定 revision 的权限/匿名访问/有效期/撤销、数据最小化；只暴露公开计划，不含聊天或 Trace。
- 后端对固定 Revision 分享与 ICS/PDF/图片等目标格式的导出契约、授权和审计。
- 后端 Commit、Schema、针对成功/冲突/拒绝/超时的测试证据；真实 Provider 使用其后端证据。

### 验收

**P0**

- AI 修改在服务端完整校验和事务成功后自动更新正式 Trip + Revision；BLOCKED 硬冲突不修改 Revision，转入明确待解决状态。
- 用户解决冲突后服务端重新校验；未解决或再次冲突不得提示成功。
- 断线/超时不能把 SSE `done` 当提交成功；操作状态查证前显示 UNKNOWN，不能重复创建隐藏版本。
- 分享内容固定创建时 Revision；后续编辑 Trip 不改变已有分享；权限、过期和撤销依后端契约呈现。
- 未落地导出契约时入口不可冒充成功；V1 ICS 不能作为 V2 固定快照导出通过。

**P1**

- AI 局部编辑不误删未提及项目；确认、取消、冲突详情可理解并适配手机。
- 分享页桌面/手机只读、可打印/可导出；复制链接、撤销/过期状态与后端一致。

### 浏览器实机场景

- 对时间冲突发起 AI 修改：展示结构化候选，选择一项后服务端复核，检查成功 Revision 或仍 BLOCKED。
- SSE 流中断、REST 成功响应丢失、重复发送：通过正式操作状态核对，不重复写且不显示假成功。
- 创建分享固定 vN 后把 Trip 更新到 vN+1；分享页仍展示 vN；非授权、过期和撤销链接的结果符合后端契约。
- 手机端冲突抽屉、分享只读页、打印/导出入口和返回导航。

### 测试建议

- `TravelV2TripAssistant.test.tsx` 与 `travelV2Assistant.test.ts`：遵循已合入实现和真实 contract，覆盖成功、pending_resolution、拒绝、超时/UNKNOWN、幂等重试。
- 新增 `TravelPendingResolutionPanel.test.tsx`：选项展示、用户明确选择、重复/非法状态。
- 新增分享/API/只读页测试：固定 Revision、授权、撤销、过期、后续修改不改变快照。
- 导出 API 测试：格式、权限、错误和快照版本；端到端在有真实后端/网关时执行。

## 开批前对账与停止条件

1. 先确认 `codex/memory-travel-v2-followup` 的 Commit/PR 与工作区是否仍在进行；按用户要求不得覆盖该后端/前端改造。
2. 将后端 Commit+Schema、Validator 覆盖、真实 Provider 证据逐项映射到 FE-2～FE-4；没有证据则保持 BLOCKED。
3. 评审确认 FE-0 报告、优先级、批次边界后，才启动 FE-1 独立分支。
4. 每批只跑 `docs/development/testing-guide.md` 选出的定向测试；真实 Provider/数据库/网关 E2E 在对应契约可用时执行。没有实际跑的测试不得写成通过。
5. 所有批次不恢复旧“应用草案”；成功自动保存只在服务端校验及事务提交后成立。
