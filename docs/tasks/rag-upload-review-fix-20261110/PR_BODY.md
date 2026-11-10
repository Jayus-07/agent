## 背景

2026-11-10 对 RAG 上传功能做前后端全链路评审，发现 1 个功能性 Bug（P1）、4 个中等问题（P2）、5 个低优先级问题（P3）。本 PR 修复全部计划内问题，并按用户决策删除 cs 端上传死代码。

## 主要修复

### P1 — 批次关联失效
- web/admin BFF 转发头白名单补 `x-batch-id`（此前浏览器路径被剥头，后端从未收到，批次聚合静默失效）+ 代理层回归测试

### P2
- body_limit 对 `/rag/upload` 改用 `RAG_MAX_FILE_SIZE + multipart 余量`，余量常量上移 `config/rag.py` 与路由层 F10 同源——贴上限文件不再被中间件 413 误拒
- PDF 扫描件预检（fitz）改 `asyncio.to_thread`，不再阻塞事件循环
- BFF 上传体 `arrayBuffer()` 整包读内存改 ReadableStream 流式透传（chunked 无 CL 预检的既有边界由 APISIX 兜底，已注释）

### P3
- 上传兜底/派发失败异常串只进日志（可能含服务器路径/连接串），客户端返通用文案 + 2 个不泄漏回归用例
- 前端 4xx 解析 JSON 的 error/detail 人话字段；失败行渲染单行原因
- 选文件即做 50MB 预检（fail-fast）
- SSE 断连后重订阅：队列已 pop 时查 Redis 镜像，命中切轮询通道续看
- okCount 遮蔽、STAGES 注释笔误、KB 列表加载失败提示

### 测试欠账收敛（既有失败，非本次引入）
- 11 个端点测试因 GATEWAY_TRUST_SECRET 退化为 guest → fixture 关闭加固（测试前提=网关已验签）
- web bff-proxy.test.ts 11 失败 + 4 个 tsc 错误（rl_client 改动后未跟上）
- pdf_ocr 与 celery_mode 的进程内模型覆盖层耦合 → autouse reset_overrides

### 死代码清理（用户决策）
- cs 端上传实现零消费方（UploadDialog/knowledge.ts/useKnowledge 及测试，-893 行）；surface 契约按 cs 域删除先例同步移除 knowledge 域

## 验证

- 后端上传链路 9 文件：213 passed, 1 skipped（基线 138 passed + 12 failed）
- web 前端：725 passed；admin：592 passed；cs：316 passed（删除后）
- 三端 tsc / production build 通过；tsc 错误净减 14（web）
- 未做（依赖合并后）：共享栈 devctl.bat rebuild 重建冒烟 + 浏览器端到端，见 docs/tasks/upload-followups-20261110/TASK.md 排期

## 遗留排期

见 docs/tasks/upload-followups-20261110/TASK.md：cs BFF 同步（cs 批量功能启用前）、状态码口径版本化、delete/reindex 错误可见性等。
