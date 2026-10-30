# REST / SSE / WS 的跨端接口与生命周期规范

> 公共契约的**开发和验收规则**；精确字段/事件枚举由后端 schema 和当前 BFF/客户端决定。不要把这里写成另一份 DTO 定义或已完成所有端口测试的证明。主 Chat、旅游 V2、客服 WS 是不同协议入口，不能强行共用事件名。

## 职责

- `backend/app/api/` 负责服务端可信身份解析、URL/DTO、HTTP/SSE/WS 错误与响应编排；Domain Service 掌握正式业务状态；前端三个独立客户端消费稳定的接口。
- `backend/orchestration/graph/event_schema.py` 是主 Chat SSE 帧定义的唯一事件事实源：核心 `meta/status/log/delta/done/error/paused`，辅助 `todo/usage/file/clarification/context/thinking/handoff`，传输保活 `ping`。**没有名为 `progress` 的主图核心事件**。领域独立 SSE 需引用该域自己的事件契约，不能套用主图枚举。
- `backend/app/api/stream_resume.py` 的主 Chat 恢复使用 `(stream_id,seq)` at-least-once；客户端按 seq 去重。重放缓存有界且进程本地；Gap 或进程重启须明确报不可恢复，不可跳帧续播。
- 冻结的通用边界以 [公共契约](../architecture/Frozen-Contracts.md) 为准；失败分类见 [统一错误](../architecture/failure-degradation.md)，前端状态见 [前端规范](../development/frontend-guide.md)。

## 新 API 最低接口契约

| 范围 | 必须声明 | 禁止 |
| --- | --- | --- |
| 输入与身份 | 请求 DTO/校验、主体/tenant 来源、资源 owner、权限点、幂等键与版本号（如适用） | 相信前端的 `user_id/role/tenant` |
| HTTP | 真实 401/403/404/409/422/429/5xx 与业务错误码；可观测 request/trace 标识 | HTTP 200 + business failed 当作成功 toast |
| 工具/MCP 错误面 | 异常对外只出稳定错误码+安全文案+trace_id（受控 `MCPToolError` 的 safe_message）；REST `ok:false` 与标准 MCP `isError:true` 分离业务失败 | 把原始异常 `str(e)`、堆栈或内部路径透传给调用方或日志 |
| 领域结果 | `success/empty/degraded/failed/blocked/unknown` 按领域现有 schema 映射 | 各领域发明不兼容的第三层通用 Result |
| SSE | 首帧、进度、输出、终帧、断线/重放语义、订阅取消；不同域的事件表单独维护 | 丢失 done/error、终帧后继续发业务帧 |
| WS | 握手后绑定可信身份和资源订阅范围、重连、顺序/去重、权限撤销 | 将租户/坐席权限从 WebSocket body 中信任 |
| 修改/写操作 | 预览/确认与正式提交边界、并发 CAS、幂等与 UNKNOWN 查询 | 自动重试未知结果的副作用或本地宣告保存成功 |
| 兼容 | Schema 字段增删、未知枚举/事件处理、发布窗口、消费者更新和回滚 | 只改 backend 不看 3 个前端/外部客户端 |

主 Chat 的事件序列：`meta` 第一帧且唯一，中段 `status/log/delta/AUX`，末尾 `done`、`error` 或 `paused` 恰有一个；`ping` 不计入内容帧、不需要重放。`paused` 专用于用户个人模型用量余额不足，表示本轮停止且保留已传输的部分内容；补足额度后重新发送，不表示从图节点恢复。Terminal 的业务成功与模型/Tool 子步骤成功不同，要检查终态结果。流连接断开不等同于 Server 已停止生成或已回滚业务操作。

企业助手实际校验后的多步骤业务计划可在节点边界移交持久任务：SSE以`done.task_id`结束本次订阅，表示已交接而非报告已完成；任务结果以`/chat/agent/tasks/{task_id}`为准。普通问答和单次长文不因字数启用该机制。任务暂停/继续走同一任务号及PostgresSaver检查点，恢复时重新校验当前授权并复用已完成节点结果；任务内个人余额不足为PAUSED，补足后可继续。运行中任务的计划可经 `/plan-revisions`（draft→apply）多轮修订（决策 #29）：仅 pending/failed 步骤可修订并以新 step_id 重发，apply 仅限任务静止（PAUSED/WAITING_USER/FAILED，FAILED 走显式 `allow_failed`），并发以 `base_plan_version` 为准（不匹配 409），恢复语义同上。此机制与上述进程内SSE重放和普通SSE的`paused`终帧不同。

## 兼容变更流程

先搜索后端 schema 和所有消费者（Web/Admin/CS、MCP/BFF、CLI、外部 API、测试），定义兼容扩展还是有意破坏，给出 API 契约版本与退役期限，先让消费端兼容再切服务端或同步发布。涉及新错误码和状态时要求**至少一个失败反例**；涉及 Auth/tenant 走 T2/T3。实际还没有统一 schema generator 时不要文档宣称存在。

## 验收清单

| 编号 | 预期 |
| --- | --- |
| AC-01 | 无身份/权限/跨租户/同租户资源 IDOR 在 API、WS、SSE 都不可获取私有数据 |
| AC-02 | HTTP 成功但业务 failed/degraded/empty 的客户端终态准确 |
| AC-03 | 401 刷新受控；写请求 UNKNOWN 不无限重投 |
| AC-04 | Chat core/AUX/terminal 帧 schema、顺序与内容符合源码 |
| AC-05 | 断线重放序号去重；Gap/重启拒绝假恢复；取消真实语义准确 |
| AC-06 | 同一用户并发编辑 CAS 冲突和幂等重复请求有确定结果 |
| AC-07 | BFF/三端受影响消费者以及旧版本兼容路径得到覆盖 |
| AC-08 | Trace 对应真实终态、错误分类与 API 版本，无敏感信息泄漏 |

规范变更不等于 AC-01～AC-08 已通过；每项提供可复现 API 记录、定向测试或浏览器证据。
