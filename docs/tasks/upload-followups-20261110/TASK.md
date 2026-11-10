# TASK: 上传链路后续排期（2026-11-10 评审遗留）

> 源自 rag-upload-review-fix-20261110 评审，已修复部分见该任务 PROGRESS.md。
> 本文件为遗留项排期：优先级 P1 > P2 > P3；建议批次见各条目。

## 遗留项与排期

### P1 — 下一迭代（合并后尽快）

| # | 事项 | 范围 | 触发条件/备注 |
|---|------|------|--------------|
| 1 | 容器级重建冒烟 + 浏览器端到端 | web/admin 前端 + 后端 | **合并本次 PR 后立即做**：`devctl.bat rebuild` + 健康确认；浏览器验证批次折叠（多文件上传）、失败原因行渲染、贴上限 50MB 文件不 413、超限文件前端即拒 |
| 2 | cs 端 BFF 代理同步 | frontend-cs `src/app/api/[...path]/route.ts` | cs 端上传死代码已删，暂无调用方；**cs 端任何批量写/上传功能启用前必须**补 `x-batch-id` 白名单 + 流式透传（对齐 web/admin） |

### P2 — 排入常规迭代

| # | 事项 | 范围 | 备注 |
|---|------|------|------|
| 3 | 上传错误状态码口径统一 | 后端 rag_upload.py + 三端前端解析 | 现状 200+`ok:false` / 422 / 409 / 403 混用；破坏性 API 契约变更，需版本化（新路径或 Accept 头协商），三端解析同步迁移 |
| 4 | delete/reindex 错误可见性 | 三端 `knowledge.ts` 的 `deleteDocument`/`reindexDocument` | `.catch(() => ({ok:false}))` 吞掉失败原因（含 403 授权拒绝），用户只能看到"删除失败"无原因；修法对齐 upload 的 4xx 解析 |

### P3 — 机会成本驱动

| # | 事项 | 范围 | 备注 |
|---|------|------|------|
| 5 | UploadDialog 组件测试补齐（web 端） | frontend web | admin 端已有 behavior 测试基建可平移；覆盖失败原因渲染/大小预检/批次提交 |
| 6 | 前端 50MB 上限与后端 env 联动 | config 暴露面 | `MAX_UPLOAD_MB` 为前端常量，后端 `RAG_MAX_FILE_SIZE` 改 env 时会漂移；可选方案：/rag/health 返回上限供前端读取 |

## 验收口径

- 每项完成需带对应回归测试；契约变更（#3）必须先写 ADR 说明迁移路径。
- cs 端（#2）启用任何上传/批量功能前，先按 web 端 bff-proxy.test.ts 补同款代理契约测试。
