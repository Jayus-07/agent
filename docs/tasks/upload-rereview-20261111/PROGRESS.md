# PROGRESS: upload-rereview-20261111（新会话交接）

## 总任务脉络

2026-11-10 用户要求评审 RAG 上传功能（前后端）→ 全部修复 → 死代码删除 → PR 合并 → 持续实施遗留排期 → 本任务（batch3）= 新会话独立复审。

## 已完成（全部已合并 main 并部署，勿重做）

| 批次 | PR | merge commit | 内容 |
|------|----|----|------|
| batch0 | #426 | d98c1c7 | 首轮评审修复：x-batch-id 白名单（web/admin）、body_limit 余量三方同源（RAG_UPLOAD_MULTIPART_OVERHEAD）、PDF 预检 to_thread、BFF 流式透传、异常脱敏、失败原因渲染、50MB 前端预检、SSE 断连后 Redis 镜像重订阅、测试 fixture 修复（GATEWAY_TRUST_SECRET） |
| — | #427 | — | docs 收尾 |
| batch1 | #430 | 3076867 | 真实链路冒烟发现 51MB 连接重置回归（undici terminated）→ BFF 路径级上限（50MiB+16KiB）三方同源 + 413 对齐 detail 字段；cs BFF 同步（白名单+流式+上限）；delete/reindex 4xx 人话解析（web 4 处 + admin 2 处复用 deleteErrorMessage） |
| batch2 | #432 | 841dc5e | 上传窗口评审：web 死代码栈删除（-1149 行，UploadDialog/useKnowledge/knowledge.ts/MultiQueryToggle 零消费方，知识库活 UI 只在 admin DocumentsPanel）；admin 三处交互修复（input value 重置 / 运行中阶段中文标签+stageMessage 渲染 / 未知 stage 兜底点亮） |

验证基线（合并部署后实测）：后端上传链路 9 测试文件 202+ 全绿；web 705 / admin 595 / cs 318；51MB 经 BFF 413+人话 detail、49MB 流式正常；批次头透传已实证到库（doc_operation_log.batch_id/user_id）；app /health commit=841dc5e，三端 200。

## 待做（排期见 followups TASK.md「执行批次计划」）

- batch3 = 本任务（复审，不改码）
- batch4：#6 env 联动 + #7 窗口可用性 + #8 SSE 断连重试 + #9 测试扩充 + batch3 快修项
- batch5：#10 lib/api multi-query 清理
- batch6：#3 状态码版本化（ADR 先行）

## 已知信息（复审重点/坑）

**架构**：浏览器 → Next BFF（三端各自 `[...path]/route.ts`：凭据收口注 X-API-Key、转发头白名单、流式透传 duplex half、/rag/upload 路径级上限 50MiB+16KiB）→ APISIX :9080 → FastAPI :8000。上传 = POST /api/rag/upload（暂存文件+确定性 upload_id+幂等键）→ Celery 或进程内回退索引 → 候选/发布（generation 隔离、发布 CAS、发布前授权复验）。SSE = GET /api/rag/upload/{id}/stream，双通道（进程内队列 / Redis 轮询镜像，终态一次写）。

**关键常量（三方同源，改一处必同步）**：RAG_MAX_FILE_SIZE（默认 50MB）+ RAG_UPLOAD_MULTIPART_OVERHEAD（16KiB）= 路由 F10 预检 + body_limit 中间件 + 三端 BFF 预检（BFF 侧是前端写死常量，联动是排期 #6）。

**三份 BFF route.ts 是历史漂移高发区**：batch0 漏了 admin/cs 的 x-batch-id；复审请逐字段 diff 三份白名单与上限逻辑。

**测试环境坑（均已处理/已知）**：
- `.env` 不入 git：新 worktree 从主仓库拷贝；GATEWAY_TRUST_SECRET 会让直连测试客户端身份退化为 guest——上传相关测试 fixture 已 patch 关闭（test_rag_upload_sync_impl / test_rag_upload_celery_mode / test_pdf_ocr autouse reset_overrides）。
- PG 双实例：agent 项目库在 docker **5433**（宿主 5432 是陈旧副本）。
- CI 偶发 npm 镜像 504（npmmirror）→ 直接 rerun。
- curl 命令行中文会被 GBK 转码（部门名用英文 id 如 customer/hr）。
- 本机测试账号：auth.users 里 `uitest_ragadmin`（admin/通用部门，user_id=442），密码已由 2026-11-10 会话重置（凭据见交接提示词，不入库）。
- 测试文件样例在 d:/tmp/smoke/（smoke-a/b.md、51m/49m bin，临时目录可能已被清理，可重建）。

**遗留风险（如实声明）**：
- 浏览器 GUI 级文件选择冒烟未做（ZCode IAB 不支持 file chooser），上传 UI 只做过 API 级端到端与组件测试。
- 后端 rag_upload.py ~2140 行单文件，复杂度高（复审可评估拆分必要性，但拆分属重构，单独排期）。
- web 端 tsc 存在 3 个既有错误（cs2/customer-service-page.test.tsx、store/chat.test.ts ×2），与上传无关。

## 工作树状态

- 本任务**无活跃 worktree**；batch0/1/2 的 worktree 与分支均已按规范清理（分支 fix/rag-upload-review-20261110、fix/upload-followups-batch1、fix/upload-dialog-batch2 已合并删除）。
- main 最新：841dc5e（PR #432）。开工按仓库规范自建 worktree：`git worktree add <dir> -b <branch> origin/main`，从主仓库拷 .env。
- 主仓库根有其他会话的未跟踪文件（docs/tasks/.mimosa、docs/tasks/trace-*、admin-catalog-refactor 等）：不触碰、不提交。

## 环境速查

| 项 | 值 |
|----|----|
| 后端 app | 127.0.0.1:8000（/health 带 build.commit） |
| APISIX | 127.0.0.1:9080 |
| web / admin / cs 前端 | 3100 / 3200 / 3300 |
| PG / Redis | 5433（agent-postgres-1）/ 6380 |
| 重建 | `devctl.bat rebuild -y`（backend+admin/web）；cs 单独 `docker compose -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.local.yml build frontend-cs && up -d --no-deps frontend-cs` |
| 后端测试 | `D:/Program Files/workplace/agent/.venv/Scripts/python.exe -m pytest backend/tests/test_rag_upload_*.py -q --no-cov`（worktree 内跑，解析到 worktree 代码） |
| git/gh 网络 | 需要时 `-c http.proxy=http://127.0.0.1:7897` 或 export HTTPS_PROXY |
