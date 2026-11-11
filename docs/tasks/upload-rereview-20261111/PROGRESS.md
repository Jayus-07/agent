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

---

## batch4/5 改动复审报告（2026-11-11 新会话，R1~R7 清单逐项，只评审不改码）

> 复审基线：origin/main=5fce573（worktree `D:/Program Files/workplace/agent-wt-upload-rereview`，detached）。
> 方法：不信任 batch4a/4b/5 的提交说明与既有结论，重读改动代码本体 + 三端 route.ts 逐字段 diff + 全仓 grep + 三个提交的测试 diff（692b34d / 623f5f0 / 6b69cc2）。

### R1~R7 结论总表（file:line @5fce573）

| 序 | 对象 | 结论 | 关键证据 |
|----|------|------|---------|
| R1 | SSE 轮询三态与异常宽限 | ✅ 通过 | 三态语义正确（`_read_progress_redis` rag_upload.py:1934-1964：`get_redis()` None→error、hgetall 空→missing、异常→error）；error 宽限 240×0.5s=120s（:1923）且期间每 30s keepalive（:1988-1991，复用 unchanged_polls 节流）；missing 保持 20 次=10s 判过期（:1919,1995-1999）；missing 分支最长 10s < 前端 idle 120s 无需 keepalive；error/missing 计数互斥重置正确（:1994,2002）；重订阅对非 missing（镜像存在或 Redis 异常）切轮询通道（:2151-2154）；与 4.6 文案兼容（三处均「上传任务不存在或已过期」） |
| R2 | 前端重连+看门狗+reader cancel | ✅ 通过 | 活跃性看门狗（idle 120s，任何字节含 keepalive 注释帧刷新 deadline，knowledge.ts:315-320,349）；绝对兜底 1900s 对齐后端（:250-251,323-325）；重连一次 + cancel 旧 reader 防队列分流（:253-290,282-288）+ cancellingForRetry 区分自我 cancel（:262,284,345,364）；终态（done/duplicate/error）handleMessage 直接 resolve 不走 fail 重连（:303-312）；服务端 keepalive 注释帧只刷 deadline 不触发 handleMessage（:352-359 只取 data: 行）✅ |
| R3 | batch_id 白名单 + SSE 文案 | ✅ 通过 | `_BATCH_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,64}")` fullmatch（rag_upload.py:457,550-552）；校验在 upload 入口 MIME 之后暂存之前（不进入上传实现，测试已断言 captured）；前端 randomId 产物（uuid 含连字符 / batch-<ts>-<rand>）均匹配；校验失败 200+ok:false 与 MIME 同口径（注释显式声明，batch6 ADR 现状素材）；文案三处（:1998,2137,2157）无 upload_id 回显 |
| R4 | health 透传 + 消费 + 三端 env 联动 | ✅ 通过 | deps.py:106-111 在 status 构造后统一添加 `max_file_size_mb`/`multipart_overhead_bytes`，四种状态形态（ready/remote/error/initializing）全部携带；UploadDialog.tsx:159-161 消费（`Number.isFinite && >0` 才覆盖，health 不可达回退 50，:12-15,68）；三端 route.ts 均读 `BFF_RAG_UPLOAD_LIMIT_BYTES || 50MiB+16KiB`（admin:138 / web:191 / cs:138）；三端逐字段 diff：上传链路区段（白名单内容、上限逻辑、413 detail、502 通用文案、duplex half）语义一致，差异仅注释文案详略 / CLIENT_APP / web 特有 travel 分享栈（SHARE_RES_HEADERS 等，web 独有业务合理） |
| R5 | 窗口可用性 | ✅ 实现（测试欠账见发现项） | beforeunload 上传中拦截（UploadDialog.tsx:130-147，依赖 [open, uploading] 保证闭包新鲜）；ESC 仅非上传中（:138-140）；遮罩 `onClick={uploading ? undefined : handleClose}`（:302）；批量 >20 提示不阻断（:378-380）；effect 回调运行时 handleClose 已完成初始化，无 TDZ 问题（useEffect 回调非渲染期同步执行） |
| R6 | 死代码删除 | ✅ 通过 | 全仓 grep（三端 src，排除 node_modules）：getMultiQueryMode/setMultiQueryMode/MultiQueryToggle/_write_published_pointer 零代码残留；仅剩三端 api/llm.ts:2 文件头注释仍提「MultiQuery 模式」（函数已删，见 P4-新1）；cs TraceDetailPanels/StepTimeline 的 MultiQuery 为 trace span 运行时展示非死代码；后端 /llm/multiquery 端点未动（范围外，符合排期） |
| R7 | 测试断言口径 | ⚠ 通过但 4.9 验收未满足 | 后端：三态适配断言（celery_mode:720-735）+ error 宽限×2（自愈续看拿终态 / 耗尽报「进度服务暂时不可用」且文案语义区分，:744-794）+ batch_id×3（非法字符/超长 65/合法 uuid）+ health 字段×1（env=100 跟随 + overhead 常量）+ 文案去 id 断言（sync_impl:782-789）；无 skip/xfail/删断言。前端：SSE 流 5 用例（502 重订 / 提前关闭重订 / 二次失败终判恰 2 次 / error 终态不重连恰 1 次 / idle 超时 cancel+重连）；behavior #9 ×3。**但 4.9 验收栏承诺的「behavior 测试 ×3」（beforeunload/ESC/遮罩/批量提示）全仓无任何用例覆盖**（grep beforeunload 仅命中实现文件）——见 P3-新1 |

### 新发现项（P1=0；P2=0；P3×1；P4×1）

#### P3-新1 batch4b #7 窗口可用性测试欠账——4.9 验收口径「behavior 测试 ×3」未满足
- 证据：TASK.md 4.9 验收栏明确「behavior 测试 ×3」；全仓 grep `beforeunload` 仅命中实现文件 UploadDialog.tsx:131（三端无任何测试文件覆盖 beforeunload/ESC/遮罩关闭/批量提示分支）；PR #444 提交说明 4.10 仅列 #9 三用例（+122 行全部为 #9）。
- 影响：实现本体经静态复审无缺陷（R5 ✅），但 #7 三个分支（uploading 拦截刷新 / 非上传中 ESC+遮罩 / >20 提示）无回归防护，后续改动 UploadDialog 时可能静默回退（本仓库 BFF/交互漂移有前科）。
- 建议：排入 batch7（与 7.3 cs CI 同 PR 或紧随其后的小 PR），补 3 个 behavior 用例（fake timers/事件派发，与既有 #9 用例同风格）。

#### P4-新1 三端 api/llm.ts 文件头注释残留「MultiQuery 模式」
- 证据：frontend{,-admin,-cs}/src/api/llm.ts:2 文件头注释仍写「LLM 业务 API：模型列表 / 切换 / 余额 / MultiQuery 模式」，而 getMultiQueryMode/setMultiQueryMode 已在 batch5 删除。
- 影响：纯注释观感（文档准确性），无行为影响。
- 建议：挂起池——任一端触碰 llm.ts 时顺手改一行。

### 观察项（复审确认非缺陷，不立项，留档）

- R2：重连成功后 `cancellingForRetry` 不重置为 false——第二次连接若异常关闭（read reject / 服务端关闭无终态），走静默 return 而非立即 fail，终判延迟到 idle 看门狗 120s 兜底触发。终判不丢（retried=true → finishFail），只是报错时机最长延迟 120s；SSE 语义下服务端仅在终态后关闭流，实际影响面极小。
- R1：missing 10s 判过期在「Redis 重启丢键（未开持久化/未故障转移）」场景会把在途任务误判过期——属罕见运维事件（既有 error 宽限已覆盖 Redis 连接抖动主场景），记挂起池观察。
- R4：health 返回的 `multipart_overhead_bytes` 前端 UI 预检未消费（UI 只用 max_file_size_mb 对比文件本体大小，语义正确——overhead 属 multipart 编码层，归 BFF env 与后端 body_limit 管）。字段为调试/未来预留，无害。

### 复审总结论

**batch4/5 三提交（#439/#444/#445）代码改动复审通过，无阻塞性发现。** P3-新1（测试欠账）与 P4-新1（注释残留）待用户确认后归入 batch7 / 挂起池；batch7 动态冒烟仍为部署级验证欠账（UNVERIFIED 清单不变）。

---

## batch7~8 施工与收尾（2026-11-11 第二段会话，接手本会话续工至全任务收口）

### 本段会话产出（4 PR 全部合并）

| PR | 批次 | 内容 | merge |
|----|------|------|-------|
| #449 | 复审 | batch4/5 复审报告（R1~R7 全过；P3-新1/P4-新1；本文件上一节） | 7b4127e |
| #450 | batch7.3/7.4 | frontend-cs CI workflow（与 admin 同款）+ happy-dom 21.0.4（修 3 个 high CVE，audit 门禁首跑 FAIL→收口） | 45f3ae5 |
| #452 | batch8 | 进度镜像可靠性：TTL 同源对齐 + int 收窄 + missing run_store DB 兜底 + worker owner 逐帧 + #7 behavior 测试×3（P3-新1 收口） | 5e242d3 |
| #454 | batch6.1 | ADR-0013 存档（**Deferred**：现状已被 batch0~4 前端解析链消化，6.2~6.4 移出排期；恢复施工前先拍板方向，建议 A 新路径版本化） | d635e3a |

### batch7 验证收口（UNVERIFIED 欠账全部闭环）

- 7.1 部署级动态冒烟（rebuild 后 build.commit 实测确认）：51MB 经 BFF 413+人话 detail / 49MB 流式受理 4.7s / SSE 真实链路（受理帧→worker 跨进程帧→终态）/ health 新字段（max=50, overhead=16384）/ batch_id 校验（非法+超长拒、合法透传）/ 重传附着语义——全过。
- 7.2 PG 用例复跑：35 全绿（19+16 两轮，上轮 16 errors 全为 5433 拒连，环境恢复后消失）。
- 7.3 cs CI：PR #450 合并即生效，CI 双 job（security 13s + functional 1m23s）全绿；本机 cs node_modules 全量重装（254 包）+ tsc 0 错 + vitest 318 全过——cs 端「零 CI 覆盖 + 本地不可验」双欠账闭环。
- 7.4 cs 代理契约基线：本地 318 含 route.test.ts，CI functional PASS 闭环。
- 冒烟测试数据清理：本会话文档零残留（索引未发布）；batch1 历史残留 smoke-a/closure-v1~v3 共 4 个已删（deleted_rows:1, degraded:false）。

### batch8 部署级实锤缺陷与修复（冒烟发现 → PR #452）

| 缺陷 | 实证 | 修复 | 部署复验 |
|------|------|------|---------|
| B(P2)：镜像 TTL 600s vs 长阶段静默 | 49M 任务 updated_at 静默 5 分钟后键 TTL=-2 消失，任务仍在跑→SSE 误报「不存在」 | TTL 改 `int(RAG_SSE_REDIS_POLL_MAX_SECONDS)` 与轮询上限同源；missing grace 耗尽后查 run_store 兜底（非终态重置宽限，终态集合以 TERMINAL_STATUSES 为单一事实源，查询异常宽大处理） | TTL=1899 ✅ |
| A(P3)：owner 单点 | worker 帧不带 owner（index_tasks.py:34）；受理帧 Redis 写失败（启动风暴实测）后 owner 永久缺失→重订阅 forbidden | worker `_redis_emit_fn(upload_id, owner=...)` 逐帧携带（复用 _get_index_identity）；写失败日志 debug→warning | worker parsing 帧带 owner=default/442 ✅ |
| （过程中新发现）float 传 EXPIRE | 首版 TTL 修复部署复验实测 TTL=-1：`RAG_SSE_REDIS_POLL_MAX_SECONDS` 是 float(1900.0)，redis-py 直传 EXPIRE 被 Redis 拒绝→ResponseError 吞掉→键永不过期（旧 600 是 int 从未暴露） | `int()` 收窄 + 测试断言 `isinstance int` 防回归 | TTL=1899 ✅ |
| P3-新1：#7 测试×3 欠账 | 复审发现 4.9 验收承诺未兑现 | behavior 测试×3（ESC/遮罩、beforeunload 上传中拦截、批量>20 提示） | admin 定向 16 passed ✅ |

验证：后端定向 48+33 passed（新增 5 用例）、admin tsc 0 错、CI 全绿（RAG Smoke / admin-observability / frontend-admin-functional）。

### 冲突分析记录（并行会话）

- 合并 #449/#450 前其他会话推了 #451（周回顾移交 docs 新文件）：三方合并试探冲突块均 0，顺序合并干净。
- 合并 #452/#454 前其他会话推了 #453（budget 域策略 API/前端）：三方试探冲突块均 0、GitHub 均 MERGEABLE，顺序合并干净；合并后 rebuild 一并把 budget 已合并代码落进共享栈（build.commit=d635e3a 实测）。

### 环境与工作树收尾

- worktree `agent-wt-upload-rereview` @ d635e3a（detached，= 最新 main），工作区干净；4 个远端临时分支 + 本地分支全清。
- 共享栈 21 容器 healthy，代码 = 最新 main；.wslconfig 生效（CPUs=4 / 5.79GiB，本会话按用户步骤重启 Docker Desktop 落地）。
- 本机 frontend-cs node_modules 已完整重装（Junction 指向主仓库，主仓库侧已补齐）。
- 上传链路任务线（batch0~8）全部收口：无活跃排期、无 UNVERIFIED 欠账；遗留仅挂起池（触发条件驱动）+ 观察项（见上节）。

### 给下一会话的提示

- 本任务线已收尾，无续工动作；若用户再提上传链路问题，先读本文件「batch7~8 施工与收尾」节 + followups TASK.md。
- ADR-0013 若恢复施工：先拍板方案方向（存档稿建议 A 新路径 `/api/rag/v2/upload`），再按 ADR「迁移路径」节执行。
- 挂起池新触发点：三端 api/llm.ts:2 注释（P4-新1）、index_run_store_pg tuple repr 拼 SQL、rag_upload.py 拆分评估。
