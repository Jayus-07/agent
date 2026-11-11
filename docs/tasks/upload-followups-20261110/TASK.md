# TASK: 上传链路后续排期（2026-11-10 评审遗留）

> 源自 rag-upload-review-fix-20261110 评审，已修复部分见该任务 PROGRESS.md。
> 本文件为遗留项排期：优先级 P1 > P2 > P3；建议批次见各条目。
>
> **进度更新（2026-11-10 batch1/batch2）**：
> - batch1（PR #430，已合并部署）：真实链路冒烟（批次头透传实证、SSE、损坏拦截、49MB 流式）；
>   发现并修复流式透传下 51MB 上传连接重置回归（BFF 路径级上限三方同源）；#2 cs BFF 同步完成；
>   #4 delete/reindex 错误可见性完成。合并后复验：51MB 经 BFF 413+人话 detail（0.05s）、49MB 合法上传正常。
> - batch2（PR #432）：上传窗口专项评审——发现 web 端整个上传栈零消费方（死代码，同 cs），已删除；
>   admin 活 UI 三处交互修复（input value 重置 / 运行中阶段中文标签+消息渲染 / 未知 stage 兜底）。
> - #5 语义变更：web 死代码删除后原「web 组件测试平移」改为「admin behavior 测试扩充」（已有 5 用例，见下方 P3）。

## 遗留项与排期

### P2 — 排入常规迭代

| # | 事项 | 范围 | 备注 |
|---|------|------|------|
| 3 | 上传错误状态码口径统一 | 后端 rag_upload.py + admin 前端解析 | 现状 200+`ok:false` / 422 / 409 / 403 混用；破坏性 API 契约变更，需版本化（新路径或 Accept 头协商），先写 ADR，前端解析同步迁移 |
| 6 | 前端 50MB 上限与后端 env 联动 | config 暴露面 | admin UploadDialog 的 `MAX_UPLOAD_MB` 与 BFF 预检常量（50MiB+16KiB）均为前端写死，后端 `RAG_MAX_FILE_SIZE` 改 env 时漂移；可选方案：/rag/health 返回上限供两处读取 |

### P3 — 机会成本驱动

| # | 事项 | 范围 | 备注 |
|---|------|------|------|
| 7 | admin 上传窗口可用性增强 | frontend-admin UploadDialog | 上传中 beforeunload 拦截（防误刷新丢进度）；ESC/遮罩点击关闭（非上传中）；批量文件数上限提示（如 >20 提示分批） |
| 8 | SSE 断连重试 | admin knowledge.ts uploadDocument | 后端已支持 Redis 镜像重订阅（#426 Fix 8），前端 SSE `fail()` 前重连一次再判「进度连接中断」，降低误报失败 |
| 9 | admin behavior 测试扩充 | UploadDialog.behavior.test.tsx | 补后台转传后重开窗口渲染失败原因、duplicate 计入成功统计、KB 加载失败态 |
| 10 | lib/api multi-query 函数清理 | frontend/src/lib/api.ts | web MultiQueryToggle 已删，`getMultiQueryMode/setMultiQueryMode` 若确认无其他消费方一并移除（需先全仓核查，含 cs2 命名空间） |

## 验收口径

- 每项完成需带对应回归测试；契约变更（#3）必须先写 ADR 说明迁移路径。
- cs 端（已对齐）启用任何上传/批量功能前，先补同款代理契约测试（已有 8 用例基线）。

## 执行批次计划（2026-11-11 排期，batch3 复审完成后细化；用户已确认）

> batch3 复审结论与 file:line 证据见 docs/tasks/upload-rereview-20261111/PROGRESS.md「batch3 复审报告」节。
> 施工方式：持续实施，每批完成后对该批改动点做一次链路评审（重读改动 + 定向测试），通过后进入下一批。
> 「侧」= 需要改动的端：B=后端 / F=前端 / BF=前后端都改。

### batch3 —— 全链路复审 ✅ 已完成（2026-11-11）

| 序 | 内容 | 结果 |
|----|------|------|
| 3.1 | 新鲜眼独立复审（后端 2149 行核心 + publish/run_store + 三端 BFF diff + admin UI + 边界） | 抽查表 14 项全过；P1=0，P2×2，P3×3，P4×4；报告已写回 rereview PROGRESS.md |

### batch4 —— P2 修复 + 快修包 + 原排期小项（拆 2 个 PR）

**PR-A：SSE 可靠性包（4.1~4.3，行为变更带回归测试）**

| 序 | 事项 | 级别 | 侧 | 文件 | 施工要点 | 验收/回归 |
|----|------|------|----|------|---------|----------|
| 4.1 | SSE 断连自动重连 | P2（#8） | F | admin api/knowledge.ts:291-295 | 连接失败/流中断先重订阅一次再判「进度连接中断」；error 终态事件不重连（那是任务失败） | behavior 测试：断连→重连→拿到终态 |
| 4.2 | 看门狗改活跃性驱动 | P2-1 | F | admin api/knowledge.ts:243,281-286 | 事件/keepalive 到达即刷新 deadline（IDLE 上限 ~120s，远大于 30s keepalive 周期）；保留绝对兜底 1900s 对齐后端 RAG_SSE_REDIS_POLL_MAX_SECONDS | 测试：长任务+keepalive 不误报；真死链超兜底报错 |
| 4.3 | Redis 空轮询异常/缺失区分 | P2-2 | B | backend rag_upload.py:1918-1964 | _read_progress_redis 区分「连接异常」与「键不存在」：异常不计入键过期证据、宽限 ~120s（期间发 keepalive）；真·空键保持 20 次/10s 判过期 | 定向测试：模拟 Redis 不可用 SSE 不提前误报终态 |

**PR-B：快修 + 体验包（4.4~4.10）**

| 序 | 事项 | 级别 | 侧 | 文件 | 施工要点 | 验收/回归 |
|----|------|------|----|------|---------|----------|
| 4.4 | admin/cs BFF 502 脱敏对齐 web | P3-1 | F(BFF) | admin/cs route.ts:172-175 | message 改通用文案「网关暂不可达」，去掉 `${String(e)}` | BFF 代理契约测试断言更新 |
| 4.5 | X-Batch-Id 白名单校验 | P3-2 | B | backend rag_upload.py:864 | `^[A-Za-z0-9_.:-]{1,64}$` 不符 422；前端 randomId 产物（uuid / batch-timestamp-rand）均匹配，前端无需改 | 定向测试：超长/非法被拒、合法透传落库 |
| 4.6 | SSE error 文案去 upload_id 回显 | P3-3 | B | backend rag_upload.py:1961,2100,2118 | 统一「上传任务不存在或已过期」 | 现有测试断言同步更新 |
| 4.7 | UploadDialog accept 补齐 5 格式 | P4-2 | F | admin UploadDialog.tsx:330 | 补 .html/.htm/.pptx/.json/.xml（对齐后端 12 格式） | behavior 快测 |
| 4.8 | 前端上限与后端 env 联动 | P2（#6） | BF | 后端 /rag/health + admin UploadDialog.tsx:14 + 三端 route.ts:188 | health 返回 max_file_size_mb + multipart_overhead；UploadDialog 轮询时消费（复用现有 2s 轮询）；三端 BFF 改读 env `BFF_RAG_UPLOAD_LIMIT_BYTES`（部署联动），未设回退写死默认 50MiB+16KiB（BFF 走接口需解决启动时序/缓存，env 方案更稳） | 定向测试：改 env 后三端预检跟随 + health 不可达回退默认 |
| 4.9 | admin 上传窗口可用性 | P3（#7） | F | admin UploadDialog.tsx:148-162 | 上传中 beforeunload 拦截；非上传中 ESC/遮罩点击可关；批量 >20 提示分批 | behavior 测试 ×3 |
| 4.10 | admin behavior 测试扩充 | P3（#9） | F | admin UploadDialog.behavior.test.tsx | 后台转传重开窗口渲染失败原因 / duplicate 计入成功统计 / KB 加载失败态 | 测试全绿 |

### batch5 —— 清理项（独立小 PR）

| 序 | 事项 | 级别 | 侧 | 文件 | 施工要点 | 验收 |
|----|------|------|----|------|---------|------|
| 5.1 | lib/api multi-query 函数清理 | P3（#10） | F(web) | frontend/src/lib/api.ts | 先全仓核查消费方（含 cs2 命名空间），确认无引用删 getMultiQueryMode/setMultiQueryMode | tsc + web 定向测试 |
| 5.2 | publish.py 死代码删除 | P4-1 | B | backend rag/indexing/publish.py:303-329 | 删 _write_published_pointer（全仓零调用方，指针由 store 方法维护）；先确认测试无引用 | 后端定向测试 |

### batch6 —— 契约变更（ADR 先行）

| 序 | 事项 | 级别 | 侧 | 文件 | 施工要点 | 前置 |
|----|------|------|----|------|---------|------|
| 6.1 | 上传错误状态码口径统一 | P2（#3） | BF | backend rag_upload.py（200+ok:false :540,546 / 422 :688 / 403 :523 / 409 :601）+ admin 解析链 | ADR（新路径或 Accept 头协商）→ 后端新契约 → admin 解析迁移 → 旧契约保留一个发布周期 | ADR 评审通过 |

### 复审后明确搁置（不进批次）

| 事项 | 级别 | 原因 |
|------|------|------|
| index_run_store_pg.py tuple repr 拼 SQL（:205,225,234,251） | P4-3 | 常量纯字符串、功能正确；batch4/5 触碰该文件时顺手改 |
| rag_upload.py ~2149 行拆分 | — | 属重构单独评估排期，不混入修复批次 |

> 批次划分原则：batch3 只评审不改码（评审与修复分离）；batch4 聚合小项降低 PR 碎片度（内部拆 SSE 可靠性包 / 快修包两个 PR）；#3 体量与风险独立成批。

---

## batch6~7 排期（2026-11-11 会话收尾时细化，接手会话：docs/tasks/upload-rereview-20261111/）

> **✅ 本节已全部执行完毕（2026-11-11 第二段会话，PR #449/#450/#452/#454 合并，main=d635e3a）。**
> 复审结论、部署级冒烟结果、batch8 缺陷修复、ADR Deferred 决策见 rereview PROGRESS.md「batch4/5 改动复审报告」与「batch7~8 施工与收尾」节。
> batch6.2~6.4 随 ADR-0013 Deferred 移出排期（现状已被前端解析链消化，属架构卫生非必修）；恢复施工前先拍板 ADR 方向。

### 前置 —— 新会话首轮：batch4/5 改动复审（只评审不改码）

| 序 | 复审对象 | 位置（file:line @main 6b69cc2） | 关注点 |
|----|---------|-------------------------------|--------|
| R1 | SSE 轮询三态与异常宽限 | backend rag_upload.py:1908-2010（_read_progress_redis/_redis_poll_events/_SSE_REDIS_ERROR_GRACE_POLLS） | 三态语义、error 分支 keepalive 节流、missing 10s 判过期；与 4.6 文案改动兼容性 |
| R2 | 前端重连+看门狗+reader cancel | admin api/knowledge.ts:239-370（IDLE_TIMEOUT_MS/MAX_TOTAL_MS/cancellingForRetry/activeReader） | 重连一次语义、cancel 防队列分流、idle 刷新时机、终态不重连 |
| R3 | batch_id 白名单 + SSE 文案 | rag_upload.py:540-548 + 1998,2137,2157 附近 | 正则口径与前端 randomId 产物兼容；文案无 id 回显 |
| R4 | health 上限透传 + 消费 | backend app/api/deps.py:78-115 + UploadDialog.tsx（maxUploadMb state）+ 三端 route.ts（BFF_RAG_UPLOAD_LIMIT_BYTES） | 四种状态形态都带字段、前端回退默认、三端 env 常量一致 |
| R5 | 窗口可用性 | UploadDialog.tsx（beforeunload/ESC/遮罩 effect + onChange 批量提示） | uploading 分支不响应 ESC/遮罩；effect 闭包引用 handleClose 的 TDZ 边界 |
| R6 | 死代码删除 | 三端 api/llm.ts + cs/admin MultiQueryToggle（已删）+ publish.py | main 上零残留（git grep 已证）；/llm/multiquery 后端端点未动（范围外） |
| R7 | 测试 | backend test_rag_upload_sync_impl/celery_mode/pipeline_init_nonblocking + admin knowledge-upload-stream.test.ts + UploadDialog.behavior.test.tsx | 断言与新契约一致、无 skip/删断言 |

### batch6 —— #3 上传错误状态码口径版本化（破坏性契约，ADR 先行）

| 序 | 事项 | 侧 | 施工要点 | 前置 |
|----|------|----|---------|------|
| 6.1 | ADR 起草 | — | 现状盘点（200+ok:false rag_upload.py:540,546 / 422 :688 / 403 :523 / 409 :601）→ 新路径 vs Accept 头协商选型对比 → 迁移路径与回滚 | **用户确认方案方向后施工** |
| 6.2 | 后端新契约 | B | 按 ADR 落新状态码契约，旧契约并行保留 | 6.1 评审通过 |
| 6.3 | admin 解析迁移 | F | knowledge.ts/deleteErrorMessage 链适配 | 6.2 |
| 6.4 | 旧契约下线 | B | 保留一个发布周期后移除 | 6.3 验证满一个周期 |

### batch7 —— 验证收口 + cs CI 补齐（2026-11-11 新发现聚合）

| 序 | 事项 | 侧 | 施工要点 | 前置 |
|----|------|----|---------|------|
| 7.1 | batch4/5 部署级动态冒烟 | 环境 | 启动 Docker 共享栈 → devctl.bat rebuild → 实测：51MB 经 BFF 413+detail、49MB 流式、SSE 重连/异常宽限、/rag/health 新字段、batch_id 校验 | Docker 可用 |
| 7.2 | 后端 16 个 PG 用例复跑 | 环境 | test_upload_resilience/to_index_e2e（上轮 16 errors 全为 5433 拒连） | 7.1 |
| 7.3 | 补 frontend-cs CI workflow | CI | 新建 .github/workflows/frontend-cs.yml（与 frontend-admin.yml 同款：tsc+vitest+security）；顺带修复本机 cs node_modules（缺 typescript） | 无 |
| 7.4 | cs 代理契约基线确认 | F(cs) | cs 启用上传/批量功能前的 8 用例代理契约基线在 CI 中跑通 | 7.3 |

### 挂起池（触发条件驱动，不主动施工）

| 事项 | 触发条件 |
|------|---------|
| index_run_store_pg.py tuple repr 拼 SQL 改占位符 | 任一 PR 触碰该文件时顺手改 |
| rag_upload.py ~2149 行拆分评估 | 单独重构排期 |
| 浏览器 GUI 级文件选择冒烟 | 需人工浏览器操作时补 |
| web tsc 3 个既有声明错误（与上传无关） | web 端常规迭代独立修 |
