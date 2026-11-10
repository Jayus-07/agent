# TASK: RAG 上传功能评审修复（2026-11-10）

## 目标

修复 2026-11-10 前后端上传链路评审发现的问题。评审范围：`backend/app/api/routes/rag_upload.py`、`backend/app/api/middleware/body_limit.py`、`frontend/src/api/{client,knowledge}.ts`、`frontend/src/components/knowledge/UploadDialog.tsx`、两端 BFF 代理。

## 范围与验收

| # | 级别 | 问题 | 修复 |
|---|------|------|------|
| 1 | P1 | BFF 代理白名单缺 `x-batch-id`，批次关联全链路失效 | web+admin 代理白名单补齐 + 代理转发头测试 |
| 2 | P2 | body_limit 对 /rag/upload 用精确上限，与路由层 F10 的 16KiB multipart 余量矛盾，贴上限文件被 413 误拒 | 中间件规则加余量并与路由常量同源 + 测试 |
| 3 | P2 | PDF 预检（check_pdf_budget/_pdf_has_text_layer）同步跑在事件循环上 | `asyncio.to_thread` 包装 |
| 4 | P2 | BFF 上传体整包 arrayBuffer() 读入内存 | 改流式透传（评估 Next 版本后实施） |
| 5 | P3 | 兜底异常信息泄露服务器路径（rag_upload.py:557/866） | 服务端留日志、客户端返通用文案 |
| 6 | P3 | 上传失败原因前端收集但不渲染；4xx 响应的错误信息未解析 | 失败行渲染后端 error 文案；4xx 解析 JSON error |
| 7 | P3 | 前端无文件大小预检 | 选文件时按 50MB 上限 fail-fast 并提示 |
| 8 | P3 | 进程内回退路径 SSE 断连后不可重订阅 | 队列缺失时回退 Redis 镜像轮询通道 + 测试 |
| 9 | P3 | 小问题：okCount 遮蔽、STAGES 注释 9→10、kbList 加载失败静默 | 顺手修 |
| 10 | 延后 | 状态码口径不一（200+ok:false / 422 / 409 混用） | 历史契约，破坏性变更需版本化，本次不动 |

约束：
- 失败详情渲染与既有「用户要求不展开详情」偏好冲突，用户 2026-11-10 明确指示「全都修」，按单行渲染后端面向用户文案实施。
- 不引入新依赖（前端组件测试需 testing-library 则不装，改由逻辑层测试覆盖）。
- 每项修复前重审整条上传链路，修复后跑相关测试；不跑默认全量回归。

## 环境事实

- worktree：`D:/Program Files/workplace/agent-wt-upload-fix`，分支 `fix/rag-upload-review-20261110`，基点 main@8648641。
- 后端测试：`backend/tests/test_rag_upload_*.py` 等；前端 vitest：`frontend/src`。
