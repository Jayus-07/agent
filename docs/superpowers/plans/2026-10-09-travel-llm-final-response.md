# 旅游助手 LLM 最终答复链实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让旅游助手每轮面向用户的自然语言答复由 LLM 基于已验证的行程事实和工具结果生成；SSE 继续展示工具执行过程，最终答复与结构化理由都显示在聊天区。

**Architecture:** 保留确定性的 Router、Supervisor、Tool Governance、数据校验和版本生命周期。旅游域图的正常出口最终经过 `travel_reporter`；工具执行过程及状态先由 SSE 展示，汇总后的白名单事实再交给 Reporter LLM 组织 `done.final_answer`，并同时显示结构化理由。SystemMessage 只包含静态指令，用户原话、工具摘要和模板事实基线放在独立 HumanMessage JSON 中。LLM 不可用或答复校验失败时回退到确定性模板，并在 metadata 中标记降级；失败/无数据结果也保留在聊天消息里。

**Tech Stack:** FastAPI、LangGraph、Python、Next.js、React、Vitest、pytest。

**Spec:** 本轮用户要求：“旅游助手全程 LLM 回复；Tool 调用结果和 SSE 过程供 LLM 组织答复，也可在页面展示。”这里的“全程”指面向用户的答复，不把路由、工具执行、审批和校验交给 LLM。

## Global Constraints

- Router、Supervisor、Tool Governance、权限、确认和行程校验继续使用确定性规则。
- Reporter 只能基于状态中的验证事实和 Tool 结果组织措辞；不得新造地点、日期、票价、时间、路线、营业状态或已应用承诺。
- Tool 事实按类别字段白名单投影；facts 快照不超过 12 KB UTF-8，完整 HumanMessage 不超过 16 KB UTF-8。
- 校验地点、日期/星期、中英文数字、预约、营业、交通与路线等事实锚点；证据不足时回退模板。
- LLM 超时、空答复、解析失败或事实校验不通过时立即回退模板，不重试并如实标记降级。
- SSE 工具事件顺序与事件契约不变；最终自然语言答复走现有 `done` 结果。
- Apply、Discard、Active 版本更新语义保持不变。

## Review Focus

- 完整成功行程中，Reporter 获得经验证的 Tool/任务结果，不能只得到一条空的聊天文本。
- 无数据或失败轮次仍显示助手说明，不得仅在页面角落显示错误后丢失会话答复。
- Reporter 回传新地点、日期/数字、预约/营业/交通/路线事实或“已应用”承诺时，事实校验必须阻止该文本并回退。
- 环境未设置开关时默认符合用户要求启用 Reporter；显式关闭仍可作为故障排查/回滚开关。
- 同一条助手消息同时带文本和 rationale 时，桌面/手机都能阅读两者；SSE 工具进度仍排在最终答复之前。

---

### Task 1: Reporter 默认启用并消费工具事实

**Files:**
- Modify: `backend/config/travel.py`
- Modify: `backend/travel/services/reporter_renderer.py`
- Modify: `backend/prompts/defaults/travel_reporter.yaml`
- Test: `backend/tests/travel/test_reporter_renderer.py`

**Interfaces:**
- 输入：`travel_reporter_node` 构建的 state，包括 `task_results`、`degraded_tools`、`blocked_tools`、行程版本/状态和确定性 Reporter 模板。
- 输出：`render_travel_reply(state, template_answer) -> (answer, reporter_meta)`；成功为 `source="llm"`，任何降级都保留模板并设置 `fallback_reason`。

- [x] **Step 1: 写失败测试**

  补测环境变量未设置时 Reporter 启用；显式 `false` 仍关闭；构造带成功/降级 Tool 结果的 state，断言 Reporter prompt 的事实输入包含任务类型、状态、结果摘要和降级说明；补充地点、中文数字、预约/路线事实边界、任务类型字段白名单、12 KB/16 KB 总字节数以及 System/Human 消息隔离；保留空输出/非法数字/模型异常回退用例。

- [x] **Step 2: 运行测试观察预期失败**

  运行：`D:\Python\python.exe -m pytest tests/travel/test_reporter_renderer.py -q --no-cov`

  预期：默认开关测试因当前默认 `false` 失败；新增事实输入断言指出缺少被审计的 Tool 结果摘要。

- [x] **Step 3: 最小实现**

  将 `TRAVEL_LLM_REPORTER_ENABLED` 默认改为 `true`，尊重用户显式设置的开/关；按 Tool 类别白名单投影受限成功摘要与降级状态，限制 facts 与完整 HumanMessage 字节数；将动态输入从 SystemMessage 移到 HumanMessage；同步更新默认 Prompt；沿用 `_validate_reply` 阻止无证据地点、数字、旅游业务事实和应用承诺。

- [x] **Step 4: 运行测试确认通过**

  重跑同一测试文件；再运行 `tests/travel/test_reporter_renderer.py` 与 `tests/travel/test_turn_decision.py`，确认 Reporter 不改变确定性决策。

### Task 2: 前端同时显示答复、理由与失败说明

**Files:**
- Modify: `frontend/src/components/travel/TravelChatDrawer.tsx`
- Modify: `frontend/src/components/travel/TravelChatDrawer.test.tsx`
- Modify if needed: `frontend/src/app/travel/page.tsx`
- Test if needed: `frontend/src/app/travel/page.test.tsx`

**Interfaces:**
- 输入：`TravelConversationMessage.text`（Reporter 最终自然语言答复）、`rationale`（结构化理由）和现有 `TravelProcessState`（SSE Tool 进度）。
- 输出：助手消息先展示 `text`，随后展示 `RationaleCard`；失败结果如有 `final_answer`，作为助手消息展示并用失败/提醒语气，不只抛页面错误。

- [x] **Step 1: 写失败测试**

  新增一条 `TravelChatDrawer` 回归：助手消息同时包含独特文本和 rationale 时，渲染树同时可见二者；新增失败结果用例：聊天请求返回 `status="failed"` 且 `final_answer` 非空时，助手消息仍展示该说明。

- [x] **Step 2: 运行测试观察预期失败**

  运行：`npx vitest run src/components/travel/TravelChatDrawer.test.tsx --testTimeout=15000`

  预期：文字被当前 `rationale` 分支遮住；失败答复被 `throw` 转成错误框，两个断言失败。

- [x] **Step 3: 最小实现**

  `renderMsg` 在同一条助手气泡里顺序渲染 Reporter 文本和理由卡；`send` 对结构化业务失败仍写入 assistant 消息，保留 `tone="warn"`/错误 tag，网络级断连仍走当前中断提示。

- [x] **Step 4: 运行组件与页面回归**

  运行：`npx vitest run src/components/travel/TravelChatDrawer.test.tsx src/app/travel/page.test.tsx --testTimeout=15000`

  预期：文字和理由均可见；失败答复不丢；历史恢复清理移交语仍通过。

### Task 3: 联合验证 LLM 答复与 SSE 展示

**Files:**
- Update: `docs/reports/2026-10-09-客服与旅游联合本机验收记录.md`
- Evidence: `docs/reports/evidence/`（仅保存不含令牌、凭据和个人敏感信息的截图）

- [x] **Step 1: 运行后端 Reporter 定向回归、前端完整 Vitest 与 TypeScript 检查。**
- [ ] **Step 2: 在本机临时前后端启用 Reporter，生成一份福州计划；确认 Reporter metadata 为 `source="llm"`，工具 SSE 过程先于最终回复，聊天区文字和 rationale 均显示。** 本机账号角色为 `viewer`，写请求返回 `PERMISSION_DENIED`；未绕过权限。
- [x] **Step 3: 复测无数据/失败回复、390/768/1280px 页面以及 Draft Apply/Discard；记录模型不可用时模板 fallback。** 失败说明和 fallback 由定向回归覆盖；响应式、Draft Apply/Discard 使用本轮前已完成的浏览器验收记录。
- [x] **Step 4: 更新验收报告和门禁状态。** 物流轨迹、Token/Cost 对账、组合任务完整分支、人工坐席、拖动排序未全部验收，ready flags 保持 `false`。
- [ ] **Step 5: 完成差异检查后，将已审核改动提交到 `codex/cs-travel-acceptance-20261009` 并推送，不直接推 `main`。**
