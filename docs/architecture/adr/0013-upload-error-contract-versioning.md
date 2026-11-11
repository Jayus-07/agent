# ADR-0013：RAG 上传错误状态码口径版本化

| 项目 | 决策 |
| --- | --- |
| 状态 | **Deferred（存档：现状 200+ok:false 混用已被 batch0~4 前端解析链消化，运行正常——属架构卫生非必修；方向 A/B 未拍板，本文建议 A 新路径版本化；恢复施工前先拍板方向并复核现状）** |
| 日期 | 2026-11-11 |
| 适用范围 | `POST /api/rag/upload`（受理）、`GET /api/rag/upload/{id}/stream`（SSE）的错误响应契约；admin 前端 `knowledge.ts` 上传解析链；三端 BFF route.ts 的上传路径级预检 |
| 不适用范围 | 成功响应（200 + `ok:true`）的载荷结构；`/api/rag/upload-failures`、`/api/rag/documents/*` 等非上传端点；SSE 事件载荷（`stage/message/doc` 结构不变） |

> **阅读警告：本文是待施工的目标决策，不是对当前生产行为的描述。** 施工前置：方案方向经用户拍板 + 本文评审通过（followups TASK.md batch6.1）。现行行为以源码为准。

## 背景与目标

上传受理端点的失败响应存在三种口径混用（batch3 复审确认，file:line @5fce573）：

| 口径 | 触发场景 | 位置 | 载荷形态 |
| --- | --- | --- | --- |
| 200 + `ok:false` | MIME 校验、X-Batch-Id 白名单、content-length 预检、上传处理异常脱敏、索引任务提交失败 | rag_upload.py:544,552,558,576,626 | `{"ok": false, "error": "..."}` |
| 4xx + 业务载荷 | 403 授权拒绝（:527,531）、409 幂等冲突（:614-619）、422 格式/魔数（:578-580） | JSONResponse | `{"ok": false, "error"}` / `{"ok": false, "conflict": true, "upload_id"}` / result 原样 |
| 503 + HTTPException detail | RAG 未就绪（deps.py:115-128）、并发槽满 ServerBusy | require_rag_ready / concurrency 中间件 | `{"detail": {"code": "SERVICE_NOT_READY", ..., "retry_after"}}` |

前端（admin knowledge.ts:193-224）因此必须同时维护两套失败模型：HTTP 层失败（`!res.ok` → 从 body 的 `detail`/`error` 字段试提取 + 拼状态码文案）与 HTTP 200 内层失败（`data.ok === false` → `data.error`）。2026-11-10 评审（batch0/1）已经两次因为口径漂移修前端解析（4xx 人话埋 JSON、503 ServerBusy 误报）。三端 BFF 还有路径级 413 预检（自己的 detail 形态），进一步放大不一致面。

目标：**一种失败、一种形态**。调用方（前端）只需要一次解析；网关、日志、Trace 可以按版本显式区分契约。

## 方案对比

### 方案 A：新路径版本化（推荐）

新端点 `/api/rag/v2/upload`（受理）与旧路径并行，共享同一实现函数；旧路径保留一个发布周期后下线。

**优点**

1. 与全局 API 规范一致（「REST 版本进路径」）。
2. 版本显式可观测：APISIX 路由、访问日志、Trace span、错误统计天然按版本区分，排障时无需解码请求头。
3. 失败显式：前端漏切路径 = 404 立即可见；Accept 头方案漏注头 = 静默回退旧契约，错位难排查（本仓库 BFF 漂移有前科）。
4. 下线机械可靠：旧契约退役 = 删路由注册；Accept 方案要删 handler 内分支。
5. 实现成本低：受理逻辑是单函数（sync_upload_impl + dispatch 链），双注册只是路由层薄壳，零实现分叉。

**缺点**

1. 双路径过渡期代码面多一组路由注册（成本约 30 行）。
2. 三端 BFF 的路径级 413 预检、SSE stream 路径白名单需同步扩 v2 前缀（漏扩 = v2 上传穿透 BFF 上限——列入施工验收）。

### 方案 B：Accept 头协商

同一端点按 `Accept: application/vnd.rag-upload.v2+json` 分支响应。

**优点**：路径不变，前端只加一个头。

**缺点**：版本不可观测（网关/日志/限流无法按版本区分）；漏注头静默回退；handler 内按头分支可读性差；本仓库无内容协商先例，引入新模式无既有基建（中间件、测试工具）支撑。

### 建议

**方案 A**。决定性理由是 2（可观测）+ 3（失败显式）：本仓库近四轮修复（batch0~4）的根因模式都是「契约静默漂移」，方案 B 会把版本决策藏回请求头里，正好复刻这个风险模式。

## 新契约设计（方案 A 前提下，错误口径两个子选项）

### 子选项 A1：只统一错误（推荐，变更面最小）

- HTTP 2xx = 受理成功，载荷 `{"ok": true, upload_id, ...}` **不动**。
- HTTP 4xx/5xx = 失败，载荷统一 `{"ok": false, "code": "<机器码>", "error": "<人话>"}`：
  - 400 参数/MIME/batch_id 非法（现 200+ok:false 三处迁入）
  - 403 授权（不变，载荷补齐 code）
  - 409 幂等冲突（不变，载荷补齐 code）
  - 413 超限（后端 body_limit 不变；载荷补齐 code）
  - 422 格式/魔数（不变，载荷补齐 code）
  - 500 处理异常（现 200+ok:false 迁入，脱敏口径不变）
  - 503 未就绪/ServerBusy（detail 结构改统一封套，`retry_after`/`ServerBusy` 进 code/data）
- 前端解析链简化为：`!res.ok → data.error`（一条路径），删 200+ok:false 分支与状态码拼文案。

### 子选项 A2：全量统一封套

成功也改 `{"code": 0, "message": "ok", "data": {...}}`（与平台统一响应结构对齐）。变更面扩大到成功载荷 + SSE 受理回执 + 幂等附着回执（attached_id 语义），前端所有消费点同步迁移。**超出 #3 的原始范围（错误口径），不建议混入本批。**

## 迁移路径与回滚

1. **6.2 后端**：v2 路由双注册共享 impl；v2 错误按 A1 口径；旧路径行为不变。回归：v1 全量现有测试保持，v2 新增口径断言（同场景 × 双路径）。
2. **6.3 admin**：`uploadDocument` 切 v2 路径 + 解析迁移（删 200+ok:false 分支）；三端 BFF 预检/白名单扩 v2 前缀（web 端上传栈已删除，仅 admin/cs）。回归：admin behavior + BFF 代理契约测试（cs route.test.ts 扩 v2 断言）。
3. **6.4 旧契约下线**：v2 上线满一个发布周期（按访问日志确认 admin/cs 旧路径流量归零）后删除旧路由注册；SSE stream 路径非本次变更对象（事件载荷未变），仅在 v2 受理回执中保持原 stream URL 兼容。
4. **回滚**：前端切回旧路径常量（单文件单常量）；后端双注册期间删除 v2 路由即恢复。无数据迁移，无持久化格式变化。

## 影响清单（施工时逐项核对）

- 后端：`backend/app/api/routes/rag_upload.py`（路由层双注册）、错误封套辅助函数、`test_rag_upload_sync_impl.py`（v2 断言）。
- admin：`frontend-admin/src/api/knowledge.ts`（uploadDocument 路径 + 解析链）、`UploadDialog.tsx` 无需改（消费 error 字符串接口不变）。
- BFF：admin/cs `route.ts` 的上传路径级上限匹配规则 + 请求体流式透传路径判断；cs `route.test.ts` 代理契约基线扩 v2。
- 网关：APISIX 无按路径白名单限流涉及 /rag/upload（如有需同步扩 v2，施工前核查）。
