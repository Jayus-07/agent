# 进度

## 已完成

- 任务分支：`codex/enterprise-assistant-roundtable-sse`；基线 HEAD：`d886f7c`。合并前发现本地 `main` 已前进到 `c3e1f97`，已确认其提交不改本任务文件，并将任务分支快进至该 HEAD 后再构建。
- `RequestBudget` 继续累计调用数，但不再因 `max_calls` 拒绝模型调用；token、金额、重试和备用模型限制保持现有行为。
- 仅 `QuotaExceeded.subject_id` 为 `user:*` 时从 GraphRunner 发出 `paused` 终态。暂停帧保留 SSE 已发出的部分文本；租户额度、配置错误等仍走错误协议。
- 前端将 `paused` 视为终态，保留过程快照与部分回答，并提供补足余额后重新发送。会话历史、模型选择、预算展示、停止/重试和功能导航继续使用企业助手现有 API/状态能力。
- `/agent` 已改为独立的 `AgentWorkspace` 展示实现；不接圆桌 API，不导入原 `/agent` 展示组件。圆桌原型只借用过程时间线、回复气泡、处理中指示和暂停提示的表现方式。
- 同步 SSE 生命周期、前端 SSE 与模型预算文档。

## 验证

- `frontend`: `npm ci --offline` 成功；`npm run build` 成功（Next.js 15.5.28，类型检查与 16 个静态页面构建通过）。构建自动改写的 `frontend/next-env.d.ts` 已恢复。
- 以本工作树的生产构建在 `localhost:3101/agent` 实际打开页面；新建页可渲染，输入文字后发送按钮启用，清空后禁用。未提交真实模型请求，避免产生业务调用或费用。浏览器控制台无错误；历史/额度 API 由于隔离运行缺少 `X-API-Key` 未能验证。
- 对 5 个改动的 Python 文件执行 `D:\Python\python.exe -m py_compile`，退出码 0。
- 从快进后的任务工作树执行 `devctl.bat rebuild /y` 成功；11 个后端镜像构建成功，数据库迁移器退出码 0。`agent-app-1`、worker、RAG 和 business-mock 为 healthy；`/health` 与 business-mock `/health` 返回 200。
- 在新 app 容器内做无 Provider 调用的运行 smoke：`max_calls=1` 时 reserve 9 次均通过且无 exceeded；`user:*` 判为暂停、`tenant:*` 不判为暂停；`paused` 帧通过契约校验。
- 未执行测试套件，也未调用真实模型，因此真实用户欠额到浏览器暂停 UI 的完整 SSE 链路仍未端到端演练。
- `git diff --check` 通过。

## 后端运行验证状态

- 用户授权后，已从本任务工作树重建共享后端；服务健康检查通过。后端新代码已部署到本机服务。
- Provider 级余额耗尽的完整 SSE 真实请求未触发，以免实际调用模型或改变额度；验证范围仅到预算拒绝、作用域分类、暂停帧契约和服务健康。

## Checkpoint 语义

- 企业助手聊天记录有服务端会话持久化；主图执行 checkpoint 当前默认关闭且没有主图恢复入口。`/chat/agent/stream/resume` 仅重放进程内 SSE 事件，不会从中断节点续跑。
- 因此余额补足后重新发送原问题；当前暂停能力不宣称恢复之前的 graph 执行。
