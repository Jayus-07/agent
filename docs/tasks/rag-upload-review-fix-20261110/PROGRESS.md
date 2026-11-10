# PROGRESS: rag-upload-review-fix-20261110

> **任务已完成并合并 main（2026-11-10）**：PR #426（merge commit `d98c1c7`），CI 8/8 绿
> （frontend-admin-functional 首跑因 npm 镜像 504 瞬态失败，重跑通过），共享栈已重建并
> 健康确认（app commit=d98c1c7，web/admin/cs 三端 200 healthy，cs 容器单独重建）。
> 合并后主仓库复跑后端上传回归 202 passed, 1 skipped；8 处修复点抽查全部在 main 生效。
> 遗留项排期见 `docs/tasks/upload-followups-20261110/TASK.md`。worktree 与任务分支已按规范清理。

## 2026-11-10（全部完成，待用户确认提交）

### 已完成修复（对应 TASK.md 编号）

| # | 修复 | 关键文件 |
|---|------|---------|
| 前置 | 11 个既有测试失败修复：`GATEWAY_TRUST_SECRET` 使测试身份头退化为 guest → fixture patch 关闭信任加固（测试前提=网关已验签） | test_rag_upload_sync_impl.py、test_rag_upload_celery_mode.py |
| 1 | P1 x-batch-id：web/admin BFF 白名单补齐 + 回归用例 | frontend{,-admin}/src/app/api/[...path]/route.ts、frontend/src/api/bff-proxy.test.ts、frontend-admin route.test.ts |
| 2 | P2 body_limit 余量：`RAG_UPLOAD_MULTIPART_OVERHEAD` 上移 config/rag.py 单一来源，中间件/路由同源引用 | backend/config/rag.py、middleware/body_limit.py、routes/rag_upload.py、tests/api/test_body_size_limit.py |
| 3 | P2 PDF 预检（check_pdf_budget/_pdf_has_text_layer）改 `asyncio.to_thread` | routes/rag_upload.py |
| 4 | P2 BFF 上传体 `arrayBuffer()` → ReadableStream 流式透传（duplex half）；chunked 无 CL 预检的边界由 APISIX 兜底（既有边界，已注释） | 两端 route.ts |
| 5 | P3 异常串脱敏：上传兜底/派发兜底不再回传异常串（可含路径/连接串），全文进日志；+2 个不泄漏回归用例 | routes/rag_upload.py、test_rag_upload_sync_impl.py |
| 6 | P3 失败原因可见：4xx 解析 JSON error/detail 人话字段；UploadDialog 失败行单行渲染原因 | frontend/src/api/knowledge.ts、UploadDialog.tsx、knowledge-idempotency.test.ts |
| 7 | P3 前端 50MB 预检：选择即拒并提示（MAX_UPLOAD_MB，权威上限仍在后端） | UploadDialog.tsx |
| 8 | P3 SSE 重订阅：队列被 pop 后查 Redis 镜像，命中切轮询通道（含终态立即重放），未命中维持拒绝语义；+2 用例 | routes/rag_upload.py、test_rag_upload_sync_impl.py |
| 9 | P3 小问题：okCount 遮蔽（改名 successCount）、STAGES 注释 9→10、kbList 加载失败提示 | UploadDialog.tsx |
| 顺带 | 既有欠账收敛：web bff-proxy.test.ts 11 失败修复（cookies stub + params Promise + 过期断言更新，tsc 错误 17→3）；pdf_ocr 与 celery_mode 的进程内模型覆盖层耦合修复（autouse reset_overrides） | frontend/src/api/bff-proxy.test.ts、backend/tests/rag/test_pdf_ocr.py |

未修（明确延后）：状态码口径不一（200+ok:false / 422 / 409 混用）——破坏性 API 契约变更需版本化，TASK.md #10。

### 验证证据

- 后端上传链路 9 文件：**213 passed, 1 skipped**（基线为 138 passed + 12 failed）
- web 前端 vitest 全量：**725 passed**；tsc 错误 17→3（剩余 3 个为未触及的既有测试文件错误）
- admin 前端 vitest 全量：**591 passed**；tsc 0 错误
- 两端 `npm run build` 生产构建通过
- 组合回归中定位并修复既有测试耦合（非本次改动引入）：celery_mode worker 用例触发 `refresh_worker_model_registry` 加载共享库 OCR 绑定 → pdf_ocr 默认路径用例误入 DB 分支

### 2026-11-10 复审（应用户要求补做）

修复完成后系统性复审发现**原评审漏了多端副本**：上传实现在三端各有一份（web / admin / cs），原评审只覆盖 web + admin 代理。复审处置：

| 端 | 复审发现 | 处置 |
|---|---|---|
| admin | 4 项同款问题仍在（4xx 拼接/大小预检/okCount 遮蔽/STAGES 注释）；失败原因内联展示与 kbList 失败提示 admin 已有实现（TD-07） | **已同步修复** + behavior 测试新增超限预检用例（3 用例绿） |
| cs | 整套上传实现（UploadDialog/knowledge.ts/batchDelete 等）**零消费方**（仅测试引用），为死代码/预留代码；BFF 同样缺 x-batch-id、arrayBuffer 整包读 | **不修**（修死代码无意义）：交用户决策——删除或明确预留后另行同步 |
| web | 首轮已修 | 复审确认交叉点无回归 |

复审交叉点逐项验证（修复间无相互破坏）：
1. BFF 流式透传 → 下游 chunked 无 Content-Length：路由 F10 预检自动跳过，流式计数精确兜底；直连网关脚本仍走 CL 预检 + 余量 ✓
2. body_limit 余量与路由 F10 同源常量，两处测试分别钉死 ✓
3. SSE 重订阅：mirror 检查位于归属校验之后（无新增暴露面）；emit_fn 写 Redis 不依赖队列存活，切通道后事件不丢 ✓
4. PDF 预检 to_thread 不改变返回语义；`_validate_ocr_page_budget` 无 IO 保持同步 ✓
5. admin behavior 测试（后台转传/KB 切换）未被改动破坏 ✓

### 最终验证证据（复审后）

- 后端上传链路 9 文件：213 passed, 1 skipped
- web 前端：725 passed；tsc 3 个既有无关错误
- admin 前端：**592 passed**（新增超限预检用例）；tsc 0 错误；build 通过
- web build 通过

### 未修复清单（如实声明）

1. **cs 端死代码**（复审新发现）：UploadDialog/knowledge.ts 零消费方，含同款问题但无实际影响面——待用户决定删除或预留
2. **状态码口径不一**（原评审明确延后）：200+ok:false / 422 / 409 混用，破坏性契约变更需版本化
3. **delete/reindex 吞错误**（超出上传评审范围）：`.catch(() => ({ok:false}))` 既有行为
4. **容器级重建冒烟 + 浏览器端到端**：待提交合并后 `devctl.bat rebuild`

### 下一步

1. 用户审阅 diff，确认后按路径限定提交到 `fix/rag-upload-review-20261110`
2. PR 合并 main（含直推场景一律走 PR）
3. 合并后主目录 `devctl.bat rebuild` 重建受影响服务并确认健康
4. 浏览器端到端冒烟：多文件上传批次折叠、失败原因展示、贴上限文件不 413
