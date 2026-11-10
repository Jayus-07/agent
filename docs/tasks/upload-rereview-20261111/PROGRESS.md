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

---

## batch3 复审报告（2026-11-11，独立新会话，只评审不改码）

> 复审基线：main@c3f07de（worktree `D:/Program Files/workplace/agent-wt-upload-rereview`，分支 fix/upload-rereview-20261111）。
> 方法：不信任既有结论，逐段重读 rag_upload.py 全文 2149 行 + publish.py 615 行 + index_run_store_pg.py 390 行 + body_limit.py + config/rag.py + 三端 route.ts 逐字段 diff + admin UploadDialog/knowledge.ts/DocumentsPanel + 边界（幂等/授权/sweeper/双通道）。

### 一、已知修复点抽查表（14 项，全部在当前 main 验证仍生效）

| # | 修复点 | 证据（file:line @c3f07de） | 状态 |
|---|--------|---------------------------|------|
| B0-1 | x-batch-id 白名单三端 | web route.ts:64 / admin:58 / cs:59（FORWARD_REQ_HEADERS） | ✅ |
| B0-2 | body_limit 余量三方同源 | config/rag.py:88（定义，不开放 env）→ body_limit.py:33 → rag_upload.py:446 | ✅ |
| B0-3 | PDF 预检挪出事件循环 | rag_upload.py:830,837（asyncio.to_thread ×2） | ✅ |
| B0-4 | BFF 请求体流式透传 | 三端 route.ts fetch(..., duplex:"half")（web:217-224） | ✅ |
| B0-5 | 后端异常脱敏 | rag_upload.py:210-213（锁竞争）、559-564（upload 入口）、872-881（流式处理） | ✅ |
| B0-6 | 失败原因人话渲染 | admin knowledge.ts:204-218（detail→error 提取链）+ UploadDialog.tsx:246,250,432 | ✅ |
| B0-7 | 50MB 前端预检 | UploadDialog.tsx:14（MAX_UPLOAD_MB=50）+ 339-344（超限过滤+提示） | ✅ |
| B0-8 | SSE 断连 Redis 镜像重订阅 | rag_upload.py:2106-2119（队列缺失→镜像存在→切 Redis 轮询） | ✅ |
| B0-9 | 测试 fixture（GATEWAY_TRUST_SECRET 退化） | test_rag_upload_sync_impl.py:542-547（patch 清空） | ✅（静态确认） |
| B1-1 | BFF /rag/upload 路径级上限 + 413 detail | 三端 route.ts（web:188-203，50MiB+16KiB + detail 字段） | ✅ |
| B1-2 | cs BFF 同步 | cs 与 admin diff 仅 CLIENT_APP/注释差异，白名单/上限/流式逐字段一致 | ✅ |
| B1-3 | delete/reindex 4xx 人话解析 | admin knowledge.ts:35（deleteErrorMessage）+ 69,350；web 4 处已随 batch2 死代码栈整体删除（自洽） | ✅ |
| B2-1 | web 死代码栈删除 | frontend/src 无 components/knowledge/、useKnowledge、lib/knowledge.ts；MultiQueryToggle 仅余 surface.test.ts:33 注释 | ✅ |
| B2-2 | admin 三处交互修复（A1/A2/A3） | UploadDialog.tsx:337（input.value 重置）/ 441-443（stageMessage 渲染）/ 415-417（未知 stage 兜底） | ✅ |

**动态验证边界（如实声明）**：本批 Docker 未启动，未重跑动态测试。论证：`git diff 841dc5e..c3f07de` 为纯 docs（3 个任务文档，+100 行，零代码变化），batch1 合并后实测基线（后端 202+ 全绿、51MB 经 BFF 413+detail、49MB 流式、三端 200）对 c3f07de 依然成立。抽查表以静态代码验证为准。

### 二、新发现项（P1：无；P2×2；P3×3；P4×4）

#### P2-1 前端 SSE 看门狗 10 分钟绝对超时，与后端长任务不匹配 → 大文件误报失败
- 证据：frontend-admin/src/api/knowledge.ts:243（`WATCHDOG_MS = 10 * 60 * 1000`，L281-286 绝对超时判失败）vs rag_upload.py:1907 起后端 Redis 轮询上限 1900s（RAG_SSE_REDIS_POLL_MAX_SECONDS，config/rag.py:551）+ admin 可设 OCR 500 页预算（UploadDialog.tsx:381，后端硬上限 RAG_OCR_MAX_PAGES_UPLOAD_HARD_LIMIT=500）。
- 影响：500 页扫描件 OCR / 超大 PDF 索引超 10 分钟时，前端判「索引超时（10 分钟）…」失败，但后端任务实际继续并最终发布成功 → 文档列表出现该文档，与「失败」提示矛盾，用户可能重传（重传=新幂等键=再次索引）。
- 建议：并入 batch4 #8（SSE 断连重试）一并改：看门狗从绝对超时改为活跃性驱动（keepalive/事件到达即刷新 deadline；SSE 有 15s keepalive，活跃连接不应被判死），保留兜底上限对齐后端 1900s。

#### P2-2 Redis 进度镜像空轮询宽限仅 10s，Redis 抖动即误报「不存在或已过期」
- 证据：rag_upload.py:1907（`_SSE_REDIS_EMPTY_GRACE_POLLS = 20`）× :1905（0.5s/次）= 10s；_read_progress_redis（:1918-1944）把「Redis 异常」与「键不存在」都折叠为 None，无法区分。
- 影响：Celery 模式 SSE 订阅期间 Redis 不可用 >10s → 直接 yield error「upload_id … 不存在或已过期」终止流，而 Worker 可能正在正常索引；前端 fail() 报「进度连接中断」（knowledge.ts:251-256）。
- 建议：并入 batch4 #8：连续空轮询上限放宽（如 60s），且对「Redis 连接异常」与「hgetall 空」区分计数（异常不算「键过期」证据）。非数据损坏风险，故 P2 下限、P3 上限之间取 P2（用户可见错误终态）。

#### P3-1 admin/cs BFF 502 响应拼接原始异常串，web 已用通用文案——三端脱敏口径漂移
- 证据：frontend-admin/src/app/api/[...path]/route.ts:172-175 与 frontend-cs 同款：`message: \`网关不可达: ${String(e)}\``；web route.ts:225-229 为通用文案「网关暂不可达」（无异常串）。
- 影响：undici 异常 toString 一般不含上游 URL（细节在 cause），泄漏面有限，但与 batch0 后端脱敏口径不一致；admin/cs 为内部端、风险低。
- 建议：batch4 快修（对齐 web 通用文案），一行改动。

#### P3-2 X-Batch-Id 后端零校验（长度/字符集），任意值直进 Celery 消息与审计库
- 证据：rag_upload.py:864（`request.headers.get("X-Batch-Id") or None` 直接采用）；全文件无格式校验；经 task_kwargs → Celery 消息、doc_operation_log.batch_id（_safe_log_op）、processing lineage（processing_batch_id）。
- 影响：超长/任意字符 batch_id 持久化进审计与 lineage 表（HTTP 头值无换行、网关有头大小上限，属数据卫生问题而非注入）。
- 建议：batch4 加白名单校验（如 `^[A-Za-z0-9_.:-]{1,64}$`，不符拒绝或截断），三端 BFF 无需改（头本就在白名单）。

#### P3-3 SSE error 消息回显客户端可控 upload_id
- 证据：rag_upload.py:1961,2100,2118（`f"upload_id {upload_id} 不存在或已过期"`）；upload_id 来自 URL path 参数。
- 影响：前端 React 文本渲染（UploadDialog :250）自动转义，无 XSS；仅观感/一致性（错误文案含用户提供字符串）。
- 建议：batch4 顺手统一文案（去掉 upload_id 回显或截断），可并入 P3-1 的快修 PR。

#### P4-1 publish.py 模块级 `_write_published_pointer` 零调用方（死代码）
- 证据：backend/rag/indexing/publish.py:303-329 定义，全仓 grep 仅定义处；发布指针实际由 store 方法维护（publish.py:404 `main_store.write_published_pointer`）。
- 建议：batch5（#10 lib/api 清理）顺手删除，属同性质死代码收口。

#### P4-2 admin UploadDialog accept 提示缺 5 种后端支持格式
- 证据：UploadDialog.tsx:330（accept 仅 .pdf,.md,.txt,.docx,.xlsx,.csv,.markdown）vs 后端 12 格式全开（parser/__init__.py:20-35 _PARSERS：另有 html/htm/pptx/json/xml，SUPPORTED_EXTS 由 PARSABLE_EXTS 派生 rag_upload.py:433-438）。
- 影响：文件选择器过滤提示不完整（「所有文件」仍可选、后端照常受理），F6 单一来源原则在前端未对齐。
- 建议：batch4 顺手补齐 accept 列表（纯提示性，无行为风险）。

#### P4-3 index_run_store_pg.py 以 Python tuple repr 拼 SQL
- 证据：index_run_store_pg.py:205,225,234,251（`{TERMINAL_STATUSES!r}` / `{ACTIVE_STATUSES!r}` 嵌入 SQL）。
- 影响：常量为纯字符串元组，当前功能正确；写法脆弱（元素含引号即坏），可读性差。
- 建议：暂不动（无行为影响），若 batch4/5 触碰该文件顺手改为 `%s` 占位或 `ANY(%s)`。

#### P4-4 rag_upload.py ~2149 行单文件复杂度
- 复审确认 PROGRESS 已声明项：文件内锁管理/幂等派发/SSE 双通道/终态收口多职责混居，但分层注释清晰、修复点定位未受阻碍。维持原结论：拆分属重构，单独排期，本批不动。

### 三、已排期项现状确认（不重复立项）

| 项 | 复审确认 | 批次 |
|----|---------|------|
| #3 状态码口径混用（200+ok:false/422/403/409） | 仍在：rag_upload.py:540,546（200）、688（422）、523-527（403）、601-607（409） | batch6（ADR 先行） |
| #6 前端 50MB/16KiB 常量写死 | 仍在：UploadDialog.tsx:14 + 三端 route.ts:188（web 行号） | batch4 |
| #7 窗口可用性（beforeunload/ESC/批量上限提示） | 未实现，转后台语义依赖页面存活（UploadDialog.tsx:148-162），刷新丢进度与 #7 描述一致 | batch4 |
| #8 SSE fail() 无重连 | 仍在：knowledge.ts:291-295（连接失败直接 fail），与 P2-1/P2-2 同源，合并施工 | batch4 |
| #9 admin behavior 测试扩充 | 现有 5 用例基线；duplicate 计入成功统计逻辑已在（UploadDialog.tsx:219,265） | batch4 |

### 四、batch4 建议施工清单（聚合，待用户确认后动手）

1. #8 SSE 重连 + P2-1 看门狗活跃性改造 + P2-2 空轮询宽限（同一次施工，knowledge.ts + rag_upload.py）
2. 快修包：P3-1（admin/cs 502 脱敏对齐 web）+ P3-2（X-Batch-Id 白名单）+ P3-3（error 文案去 id 回显）+ P4-2（accept 补齐）
3. #6 env 联动（/rag/health 返回上限，UploadDialog + 三端 BFF 消费）+ #7 窗口可用性 + #9 测试扩充（按原排期）
4. batch5：#10 + P4-1（publish.py 死代码删除）
5. batch6：#3（不变）

### 五、复审亮点（值得保留的设计，抽查确认无新引入缺陷）

- 幂等附着语义闭环：`IdempotencyKey` 含 actor（backend/shared/idempotency.py:1435-1440）→ 跨用户同内容不会互相附着，SSE owner 校验（rag_upload.py:2019-2037）与附着路径自洽；`_resolve_replayed_dispatch`（:1400-1435）对「重放命中死任务」重开新一轮候选，SSE 不会永久悬挂。
- 发布协议失败语义分层正确：提交点前失败→清理候选+failed；提交点后失败→保持 publishing 续跑（publish.py:507 CAS 提交点、rag_upload.py:1045-1052 / 1861-1866 与 mark_status 终态一次写 index_run_store_pg.py:192-215 互相咬合）；duplicate 终态重放走 `status=%s` 幂等分支不抛 RunStateConflict。
- 授权三道闸完整：上传裁决（can_upload_to，authz.py:176-198）→ 发布前复验（_reverify_publish_authorization，rag_upload.py:1588-1628，#65）→ 管理裁决（can_manage_row 按行归属）。
- 清理链完整自愈：启动锁/tmp 清理（cleanup_stale_upload_artifacts，server.py:390-393 接线；非终态暂存豁免 :337-343）+ 进度队列定时 GC（progress_queue_gc_loop，server.py:426-427）+ 一致性 sweeper（server.py:539-547）。

### 六、排期确认与落盘（2026-11-11 用户确认后）

- 用户确认：batch4 拆 2 个 PR（SSE 可靠性包 / 快修+体验包）；排期细表（含前后端归属「侧」标注与验收口径）已落盘更新至 docs/tasks/upload-followups-20261110/TASK.md「执行批次计划」节。
- 工作方式确认：持续实施，每批完成后对该批改动点做一次链路评审（重读改动 + 定向测试），通过后进入下一批。
- batch3 报告 + 排期落盘以同一文档 PR 提交合并。

---

## 施工进度（2026-11-11 持续实施，batch3 报告确认后）

### batch4 已完成并合并（SSE 可靠性包 + 快修+体验包）

| PR | 内容 | 验证 |
|----|------|------|
| #439（batch4a，merge 692b34d） | 4.3 后端 _read_progress_redis 三态(ok/missing/error)+异常宽限 120s；4.1/4.2 admin knowledge.ts 看门狗活跃性驱动(idle 120s+兜底 1900s)+SSE 重连一次 | 后端定向 80 passed；admin 全量 600 passed。批内评审发现并修复「重连前 cancel 旧 reader 防队列事件分流」 |
| #444（batch4b，merge 623f5f0） | 4.4 admin/cs BFF 502 脱敏；4.5 X-Batch-Id 白名单；4.6 SSE 文案去 upload_id 回显；4.7 accept 补齐 5 格式；4.8 /rag/health 透传上限+UploadDialog 消费+三端 BFF 读 BFF_RAG_UPLOAD_LIMIT_BYTES；4.9 窗口可用性(beforeunload/ESC/遮罩/批量提示)；4.10 behavior 测试+3 | 后端定向 141 passed；admin tsc 0 错；admin 603（602+design-tokens 偶发超时单独复跑过）；web tsc 3 既有错误无新增；cs tsc 本地依赖不全 UNVERIFIED（CI 补验） |

**批后链路评审（batch4 整体）**：三端 BFF 改后逐字段 diff 一致（仅注释/CLIENT_APP 差异）；batch4a 三态与 batch4b 文案改动兼容（141 passed 佐证）；幂等/发布链路零触碰。

**新发现（P3，排期外）**：.github/workflows 无任何 frontend-cs 专属 CI（7 个 workflow 零引用 cs）——cs 端 tsc/vitest/构建完全无 CI 覆盖，且本机 cs node_modules 不完整（缺 typescript）。建议后续补 cs CI workflow（与 frontend-admin.yml 同款）。

### batch5 已施工（清理项，待 PR）

- 5.1：三端 api/llm.ts 删 getMultiQueryMode/setMultiQueryMode（经 lib/api.ts 星号转发的 #10 对象；全仓核查唯一消费方 = MultiQueryToggle 组件，而该组件三端零消费方——cs/admin 死代码与 batch2 删除的 web 版同款，已一并删除）。
- 5.2：backend/rag/indexing/publish.py 删 _write_published_pointer（303-329，全仓零调用方，发布指针实际由 store 方法维护）。
- 验证：admin 全量 603 全过+tsc 0 错；web tsc 3 既有错误无新增；后端 publish/索引定向 39 passed + 16 errors 全为 PG 5433 连接拒绝（Docker 未启动环境性，非代码性）；publish.py py_compile 过。

### 过程事故记录（如实）

- 批后评审时误执行 `git checkout main -- .`（本地 main 停在 c3f07de），把旧版文件污染进工作区+暂存区，导致 batch5 首轮验证出现 4 个假失败（SSE 测试因 knowledge.ts 被回退）。处置：git reset（mixed）+ git checkout HEAD -- .（未用 --hard），在干净基线重做 batch5 删除并复跑验证全绿。教训：`git checkout <branch> -- .` 在并行 worktree 场景等同覆盖工作区，后续一律禁用（用分支切换 `git checkout <branch>` 而非路径检出）。

---

## 会话收尾（2026-11-11 batch3~5 会话交接）

### 本会话产出汇总

| PR | 批次 | 内容 | 状态 |
|----|------|------|------|
| #434 | batch3 | 复审报告+排期落盘 | 已合并 |
| #439 | batch4a | SSE 可靠性包（后端三态宽限 / 前端重连+活跃性看门狗+reader cancel） | 已合并 |
| #444 | batch4b | 快修+体验包（batch_id 校验/文案脱敏/上限联动/窗口可用性/#9 测试） | 已合并 |
| #445 | batch5 | 死代码收口（multi-query 三端+publish.py 死函数） | 已合并 |

已合并远端分支 ×4 已删除；批后链路评审完成（三端 BFF 一致、死代码清零）。排期权威：followups TASK.md「batch6~7 排期」节（含新会话复审清单 R1~R7）。

### 工作树状态（接手必读）

- worktree：`D:/Program Files/workplace/agent-wt-upload-rereview`，分支 fix/upload-rereview-batch5（已合并，本会话收尾 docs 提交后可删）。开工新任务按规范自建新分支。
- **node_modules Junction ×3**：worktree 的 frontend{,-admin,-cs}/node_modules 是指向主仓库同名目录的 Junction（worktree 无独立依赖）。本地跑测试用 `./node_modules/.bin/vitest.CMD / tsc.CMD`；若删除重建 worktree 需重新建 Junction（PowerShell New-Item -ItemType Junction）。
- frontend-cs 本机 node_modules 不完整（缺 typescript）：cs 端 tsc 本地跑不了，依赖 CI 或重装。
- 主仓库根有其他会话未跟踪文件：不触碰、不提交。

### 接手会话第一步

1. `git status` 核对工作区干净（本会话曾发生 `git checkout main -- .` 污染事故已恢复——**该命令禁用**，分支切换用 `git checkout <branch>` 不带 `-- .`）。
2. 按 TASK.md R1~R7 复审清单以新鲜眼复审 batch4/5 改动，结论写回本 PROGRESS.md。
3. 复审通过 → batch7（7.1 需启动 Docker）→ batch6.1 ADR（方案方向需用户拍板：新路径 vs Accept 头协商）。

### 遗留验证欠账（如实）

- batch4/5 全部改动未做部署级动态冒烟（Docker 未启动）：51MB 413 / 49MB 流式 / SSE 真实链路 / health 新字段 / batch_id 校验实测均 UNVERIFIED，batch7.1 闭环。
- 后端 16 个 PG 依赖用例上轮为连接拒绝（环境性），batch7.2 复跑。
- cs 端 tsc/vitest 本地无法跑（依赖不全 + 无 CI），batch7.3 补 CI 后闭环。
