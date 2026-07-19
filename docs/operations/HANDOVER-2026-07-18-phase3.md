# Handover 2026-07-18 Phase 3 — 文档操作中心（链路追踪）

> 上一会话：Phase 1+2 已提交（commit 2271671，feat/knowledge-batch-ops）。本会话做 Phase 3，**后端完成未验证，前端未完成**。额度耗尽中断。

## 一句话状态

Phase 3 = 文档管理独立窗口，记录"谁上传/重索引/删除了哪个文档"+ 关联 trace 链路（6 span 耗时）。**后端代码全写完且已重启生效，前端只加了类型没加方法/页面**。下一步：补前端 → 验证 → commit。

---

## 当前分支与提交

- 分支：`feat/knowledge-batch-ops`（从 master 开，未合并）
- 已提交：`2271671` — Phase 1+2（后端索引链路修复 + 前端批量功能）
- **未提交**：Phase 3 全部改动（后端 + 前端类型）

---

## Phase 3 进度

### ✅ 后端（全完成，已重启生效，端口 8000 running）

| 文件 | 改动 |
|------|------|
| `backend/rag/indexing/operation_log.py` | **新建**：`doc_operation_log` 表 + `DocumentOperationLogger` 类（log + list），仿 `doc_registry.py` |
| `backend/config/database.py` | 加 `DOC_OPERATION_LOG_PATH = "data/doc_operation_log.db"` |
| `backend/config/__init__.py` | 加 `DOC_OPERATION_LOG_PATH` 到 import + `__all__` |
| `backend/rag/indexing/indexer.py` | `_index_file` 成功时 `return trace.id`；`reindex_file` 返回值加 `"trace_id"` |
| `backend/app/api/routes/rag.py` | import + `_get_op_logger()` + `_extract_source(request)` + `_safe_log_op()` + upload/reindex/delete 三处埋点 + `GET /rag/operations` |

**后端 py_compile 通过，uvicorn 启动正常**（`Application startup complete`，已处理 200 请求）。

**埋点细节**：
- upload：`upload_document(request, file)` 提取 source → 传给 `_run_index_background(..., source)` → `_do_index_sync` 返回 `reindex_file` 结果（含 trace_id）→ `_safe_log_op("upload", trace_id=...)`。duplicate 分支返回 `{"trace_id":"","duplicate":True}`（无 trace）。
- reindex：`reindex_document(doc_id, request, force)` → `reindex_file` 拿 trace_id → log
- delete：`delete_document(doc_id, request)` → log（trace_id=None）
- `_safe_log_op` 包 try/except，审计日志写挂不阻断业务

### ❌ 前端（只加了类型，方法/页面/入口都没写）

| 文件 | 状态 |
|------|------|
| `frontend/src/services/knowledge.ts` | ✅ 加了 `OperationType` / `OperationLog` / `OperationListResult` 类型（在 `DocumentListResult` 后）。**❌ 没加 `getOperations` 方法到 `knowledgeService` 对象** |
| `frontend/src/app/knowledge/operations/page.tsx` | ❌ 未创建 |
| `frontend/src/components/Sidebar.tsx` | ❌ 未加"文档操作"入口 |

---

## 下次会话必做（按顺序）

### 1. 前端 knowledge.ts 加 getOperations 方法

在 `knowledgeService` 对象里 `getChunks` 方法后加（类型已就位）：

```typescript
async getOperations(params?: {
  page?: number; page_size?: number; operation?: string; doc_id?: string
}): Promise<OperationListResult> {
  const qs = new URLSearchParams()
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== '') qs.set(k, String(v))
    })
  }
  const res = await fetch(`${BASE}/operations?${qs}`)
  return res.json()
},
```

### 2. 新建 `frontend/src/app/knowledge/operations/page.tsx`

仿 `frontend/src/app/knowledge/documents/page.tsx` 的表格 + 分页模式。关键点：
- 表头：时间 / 文档名 / 操作徽章 / 操作人 / 结果 / 操作列
- 操作徽章颜色：upload 绿 / reindex 蓝 / delete 红
- 操作列：`trace_id` 非空 → `<Link href={/observability/traces/${trace_id}}>查看链路</Link>`（跳现有 trace 详情页，复用 StepTimeline/FlameGraph）；为空 → 灰显"无链路"
- 顶部过滤：按 operation 类型（全部/upload/reindex/delete）
- `detail` 字段是 JSON 字符串或对象，展示时 `JSON.stringify` 或取关键字段（chunk_count/error）
- 用 `useToast` 不用 alert
- `'use client'`（有 useState）

### 3. Sidebar 加入口

`frontend/src/components/Sidebar.tsx` 知识库分组加 `{ label: '文档操作', path: '/knowledge/operations' }`。

### 4. 后端 API 验证（我来跑过的部分要复核）

```bash
# 上传一个新文件
curl -s -F "file=@/tmp/test.md" http://localhost:8000/rag/upload
# 确认操作日志 + trace_id 非空
curl -s "http://localhost:8000/rag/operations?page=1&page_size=5" | python -m json.tool
# 用返回的 trace_id 跳转，确认能拿 6 span
curl -s "http://localhost:8000/observability/traces/{trace_id}" | python -m json.tool
# reindex 一篇 → 确认 reindex 记录
# delete 一篇 → 确认 delete 记录 trace_id=null
# 重启后端 → GET /rag/operations 确认日志仍在（持久化），trace 跳转 404（内存清了，best-effort 符合预期）
```

### 5. 前端验证

```bash
cd frontend && npx tsc --noEmit
# vitest 有 7 个 pre-existing 失败（P1.5 MockEventSource mock 问题，非本会话引入），可忽略
```

### 6. 浏览器手工验收 + commit

验收点：操作列表显示 / 徽章颜色 / "查看链路"跳 trace 详情页显示 6 span 耗时 / delete"无链路" / 重启后日志还在但 trace 过期。

commit（在 feat/knowledge-batch-ops 分支）：
```
feat(knowledge): 文档操作中心 + 链路追踪关联

- 后端 operation_log.py：持久化操作审计日志（upload/reindex/delete）
- indexer 透出 trace_id，操作日志关联 trace 链路
- GET /rag/operations 查询接口
- 前端 /knowledge/operations 独立窗口 + Sidebar 入口
- "查看链路"跳转现有 /observability/traces/{id}（复用 StepTimeline/FlameGraph）
```

---

## 关键约束（设计决策，别推翻）

1. **trace 纯内存**（`trace_collector` deque maxlen=200，重启丢）。操作日志持久化 SQLite，trace 关联 best-effort——近期操作能跳转，老操作/重启后 trace 404 是预期行为。
2. **无 auth**（[AUTH_TODO.py](../../backend/shared/AUTH_TODO.py)）：`user_id` 默认 `anonymous`，"谁"=anonymous + IP/UA。auth 接入后升级，schema 不变。
3. **delete 无 trace**：DELETE 不创建 trace，trace_id 留空。
4. **不在操作中心内嵌 span 渲染**：直接跳转现有 `/observability/traces/{id}` 页面复用。
5. **不做实时推送**：刷新查看。

---

## 服务状态

- 后端：**running**（端口 8000，新代码，无 --reload）
- 前端：**running**（端口 3000，Next.js dev HMR）
- 启动命令见 `docs/operations/uvicorn-restart-recipes.md` 或 `start_all.bat`

## 关键文件位置

- 计划文件：`C:\Users\wh\.claude\plans\enumerated-shimmying-journal.md`（Phase 3 完整方案）
- 本文档：`docs/operations/HANDOVER-2026-07-18-phase3.md`
- 上一会话交接：`docs/operations/HANDOVER-2026-07-18.md`（Phase 0 SSE，已完成）

## 提示词模板（下次会话第一句）

> 上次会话交接在 `docs/operations/HANDOVER-2026-07-18-phase3.md`。
> Phase 3 后端完成已生效，前端只加了类型没加方法/页面。
> 第一步：读交接文档 → 补前端 getOperations + operations/page.tsx + Sidebar → 后端 API 验证 → tsc → 浏览器验收 → commit。
