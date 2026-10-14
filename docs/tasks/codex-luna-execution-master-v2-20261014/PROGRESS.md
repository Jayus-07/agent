# PROGRESS

- 更新时间：2026-10-14。
- 当前阶段：A / Conversation-first 主助手。
- 基线：`bf3cddf7ac7329f576f036cb1a9717a8f96dfd27`；分支：`codex/main-assistant-conversation-first-20261014`。
- 状态：源码与定向验证完成，准备提交并创建 Draft PR；A 的真实模型与 APISIX/浏览器 T3 尚未通过，按协议标记 `BLOCKED`，随后自动进入 B。

## A 实现

- `/chat/agent*`、`/chat/customer-service*` 和对应 `/memory/*` 路由由服务端注册路径绑定产品身份；不信任 body 的用户、产品、域提示；产品入口要求可信用户与租户。
- Session、stream、memory user、幂等键均按产品隔离；Resume 与 Abort 校验同一可信用户/租户；能力候选和执行点继续双重检查产品范围。
- 主助手现有 general_chat 路径接管明确的一般概念、地点、日常对话与无活跃域的短追问；避免进入分类/embedding 路由；客服及内部业务语义仍保留原路由。
- 普通 `/chat/*` 共享入口按公共产品处理并禁止持久记忆；主助手与客服前端改用产品专属 Chat/Memory API。
- 未修改旅游实现、`frontend-admin/`、退款业务和生产/共享数据库。

## 验证证据

- 后端定向 T2：8 个测试文件，`142 passed`；运行命令为 `python -m pytest ... -q --no-cov -m 'not pg'`，使用独立临时 PostgreSQL 容器提供本地测试依赖，未执行 PostgreSQL 标记测试。
- 主前端：聊天/记忆 API 与 SSE Hook 定向测试 `6 passed`；`npx tsc --noEmit` 通过。
- 客服前端：新增 Chat/Memory 路由测试 `3 passed`；`npx tsc --noEmit` 通过。
- Python `py_compile`、`git diff --check` 通过。前端依赖由本机 npm 缓存离线安装。
- 未使用真实模型 API 或额外付费测试额度；无 APISIX、浏览器及独立生产等价环境端到端证据。

## 阻断与下一步

- **T3 BLOCKED：**用户反馈 Clash 开启后真实模型 API 无法连接。未尝试改代理或绕过连接，也未发起任何模型调用。需要可达的 Provider/API 网络和隔离的 APISIX/浏览器集成环境后补验。
- **客户端整合待办：**现有 Admin 客户端仍调用共享 `/api/chat/stream` 与 `/api/memory/sessions`；共享入口现在按公共产品处理，持久记忆返回 403。根据范围不修改 `frontend-admin/`；由 Admin 会话适配到确定的产品专属路径后再做联合验收。
- A 以 Draft PR 保留代码与 CI 证据，不报告 MERGE_READY。按 EXECUTION MASTER V2 继续 B：先以独立 worktree 对 PR #61 运行真实隔离 PostgreSQL 门禁；不触碰其余活动 worktree或共享数据库。
