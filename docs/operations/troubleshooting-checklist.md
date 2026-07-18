# Trace System Troubleshooting 清单

> 改完 trace 系统任何一部分后的标准验证流程。每次 backend / frontend 改动必跑。

---

## 1. 修改后的代码确认加载

```bash
# Kill 所有 Python 进程
powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"

# 等端口释放
sleep 4
powershell -Command "(Get-NetTCPConnection -LocalPort 8000 -State Listen | Measure-Object).Count"
# 期望：0

# 干净启动
cd backend
nohup ../.venv/Scripts/python.exe -m uvicorn app.server:app --port 8000 > /tmp/uvicorn.log 2>&1 &
sleep 10

# 确认新 worker 启动
grep "Started server process" /tmp/uvicorn.log | tail -2
powershell -Command "(Get-NetTCPConnection -LocalPort 8000 -State Listen | Measure-Object).Count"
# 期望：1
```

⚠️ **如果只看到 `Reloading...` 没有 `Started server process` → reload 失败**，见 [`uvicorn-restart-recipes.md`](uvicorn-restart-recipes.md)。

---

## 2. SSE 后端 raw 验证

```bash
# 上传测试文档（确保 SHA256 与现有不同，否则是 duplicate 路径）
echo "测试内容" > /tmp/sse_check.txt
UPLOAD=$(curl -s -X POST http://localhost:8000/rag/upload -F "file=@/tmp/sse_check.txt")
UPLOAD_ID=$(echo "$UPLOAD" | python -c "import json,sys; print(json.load(sys.stdin).get('upload_id',''))")
echo "upload_id: $UPLOAD_ID"

echo "=== SSE raw output ==="
timeout 8 curl -s -N "http://localhost:8000/rag/upload/${UPLOAD_ID}/stream" | head -10
```

### 期望输出（Phase 1.5 后）

```
data: {"stage": "uploading", "message": "..."}

data: {"stage": "parsing", "message": "..."}

data: {"stage": "chunking", "message": "..."}

data: {"stage": "embedding", "message": "..."}

data: {"stage": "writing", "message": "..."}

data: {"stage": "done", "message": "...", "doc": {...}}
```

### 检查清单

- [ ] 每个 `data:` 行**没有** `event:` 前缀
- [ ] 每个 `data:` JSON 包含 `"stage": "<name>"`
- [ ] 6 个 stage 都出现（uploading / parsing / chunking / embedding / writing / done）
- [ ] 中文消息以 `\uXXXX` 转义（不是乱码字节）

### 重复文档变体（duplicate stage）

```bash
# 上传相同文件 → expect 'duplicate' 而不是完整 6 stage
UPLOAD=$(curl -s -X POST http://localhost:8000/rag/upload -F "file=@/tmp/sse_check.txt")
UPLOAD_ID=...
timeout 6 curl -s -N "http://localhost:8000/rag/upload/${UPLOAD_ID}/stream" | head -3
```

期望：

```
data: {"stage": "uploading", ...}
data: {"stage": "duplicate", "message": "文件已存在，未重复索引", "doc": {...}}
```

---

## 3. 前端 EventSource 行为

### DevTools 验证流程

1. **强制刷新浏览器**：`Ctrl + Shift + R`（清缓存）
2. 打开 Knowledge 页面
3. **上传一个文件**
4. **DevTools → Network → 找到 `/api/rag/upload/{id}/stream`**：
   - **Headers**：`Content-Type: text/event-stream`
   - **Response** body：每行 `data: {...}` 含 `stage` 字段
5. **DevTools → Console**：上传时不应有 error 日志
6. **DevTools → Application → Local Storage / Cookies**：不需要特殊检查

### 检查清单

- [ ] 上传成功 → 上传 6 阶段进度依次显示
- [ ] 上传完成 → 弹窗关闭 + 文档列表刷新
- [ ] 重复上传 → 显示"文档已存在"提示（非一闪而过）
- [ ] 网络断开 → 显示"进度连接中断"（这是预期错误）
- [ ] 上传非法格式（.exe）→ 显示错误提示

---

## 4. Trace 数据持久化（手动检查）

```bash
# 验证 trace 仍记录到 TraceCollector
# 通过 observability API（或直接读 in-memory 状态）
curl -s http://localhost:8000/api/observability/traces | python -m json.tool | head -30
```

### 检查清单

- [ ] trace 数 > 0
- [ ] 每个 trace 有 `workflow_kind` 字段（`rag_query` / `knowledge_index`）
- [ ] span 数 ≥ 6（knowledge_index）
- [ ] span.metrics 包含 `cost_usd`（如果用了 LLM）
- [ ] span.kind 是合法 SpanKind enum 值

---

## 5. 索引器（incremental indexer）

```bash
# 上传新文件 → 验证 SHA256 changed → added/modified
echo "新内容 $(date)" > /tmp/test_index_new.txt
UPLOAD=$(curl -s -X POST http://localhost:8000/rag/upload -F "file=@/tmp/test_index_new.txt")
UPLOAD_ID=...
echo "expect 6 stages: uploading, parsing, chunking, embedding, writing, done"

# 再次上传相同内容 → expect 'duplicate'
UPLOAD2=$(curl -s -X POST http://localhost:8000/rag/upload -F "file=@/tmp/test_index_new.txt")
UPLOAD_ID2=...
echo "expect 2 stages: uploading, duplicate"
```

### 检查清单

- [ ] 新文件 → 6 个 stage
- [ ] 相同文件 → `duplicate` stage（不再 6 阶段）
- [ ] 修改文件（不同 SHA256）→ `modified` → 重新 6 阶段
- [ ] 文档列表 API 反映新文档（无需手动刷新）

---

## 6. RAG 查询 Trace

```bash
# 上传一个文档后，等几秒让它索引
# 然后问一个相关问题
curl -s -X POST http://localhost:8000/api/rag/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "测试问题", "session_id": "test_session"}'
```

### 检查清单

- [ ] 响应含 `answer` 字段
- [ ] 响应含 `references` 字段（含来源）
- [ ] TraceCollector 有新 trace（`workflow_kind=rag_query`）
- [ ] Trace 含 `llm_generate` span（kind=llm）
- [ ] `llm_generate` span.metrics 包含 `cost_usd`、`finish_reason`

---

## 7. 单元测试 + 集成测试

```bash
# 后端
cd backend
D:/Python/python.exe -m pytest tests/ -q

# 前端
cd frontend
npx tsc --noEmit  # 类型检查
# npm test  # 跑 vitest（如果装了 vitest）
```

### 检查清单

- [ ] 后端 pytest 全过（>140 tests）
- [ ] 后端 tracer / supervisor / indexer_trace / llm_span_fields 覆盖 >95%
- [ ] 前端 tsc --noEmit 通过
- [ ] 前端 vitest 覆盖 knowledge.ts 关键路径

---

## 8. 部署前最终清单

- [ ] 所有改动已 commit + push
- [ ] 已更新 `docs/observability/sse-pipeline-debugging.md`（如果改 SSE）
- [ ] 已更新 `docs/operations/uvicorn-restart-recipes.md`（如果改 restart 脚本）
- [ ] crg MCP detect_changes 跑过（`mcp__code-review-graph__detect_changes_tool`）
- [ ] crg MCP get_affected_flows 跑过（如果改 orchestration）
- [ ] 前端 dev server 起动后手动验证一遍（`docs/development/testing.md` §15.1）

---

## 常见反模式（绝对不能做）

❌ **`pop('stage')` 在 SSE emit 前** —— 数据丢失，前端拿不到 stage  
❌ **`event: xxx\ndata: {...}` 输出 SSE 消息** —— 前端 onmessage 收不到  
❌ **`ensure_ascii=False` 输出的 SSE** —— 客户端按默认编码乱码  
❌ **`os.path.join` 不 normpath 就用** —— Windows 路径分隔符不一致  
❌ **uvicorn --reload 测试关键 bug** —— reload 经常卡，行为不可信  
❌ **删除 listener 不 unsub** —— 内存泄漏 + 影响下次 upload  

---

## 关键代码位置速查

| 文件 | 改什么时看这里 |
|------|----------------|
| `backend/rag/tracer.py` | TraceCollector / WorkflowKind / SpanKind / subscribe() |
| `backend/orchestration/observability.py` | GRAPH_TOPOLOGY / INDEXING_TOPOLOGY |
| `backend/rag/indexing/indexer.py` | 6 span 埋点位置 |
| `backend/rag/chain.py` | `_timed_stuff` LLM span 注入 cost/prompt |
| `backend/app/api/routes/rag.py` | SSE encode / event_stream / upload / duplicate 检测 |
| `backend/infra/llm/proxy.py` | `_last_call_meta`（token + finish_reason + cost） |
| `backend/infra/llm/models.py` | `compute_cost_usd` pricing 表 |
| `frontend/src/services/knowledge.ts` | `uploadDocument` SSE 消费 |
| `frontend/src/components/knowledge/UploadDialog.tsx` | 上传弹窗 UI |

---

## 何时升级文档

| 改动 | 文档 |
|------|------|
| 改 SSE 协议（编码、字段、阶段） | `sse-pipeline-debugging.md` |
| 改 uvicorn 启动脚本（bat） | `uvicorn-restart-recipes.md` |
| 改 Trace 数据模型（WorkflowKind / SpanKind） | `trace-model.md`（已有） |
| 改前端 SSE 消费逻辑 | `sse-pipeline-debugging.md` 第 7 节 |
| 改 start_all.bat / restart_all.bat | `uvicorn-restart-recipes.md` |
| 新增阶段或新业务（duplicate → 新 stage） | `sse-pipeline-debugging.md` Bug 3 |

---

## 关联文档

- [`sse-pipeline-debugging.md`](../observability/sse-pipeline-debugging.md) —— 7 类 SSE bug 排查
- [`uvicorn-restart-recipes.md`](uvicorn-restart-recipes.md) —— reload 卡住处理
- [`trace-model.md`](../observability/trace-model.md) —— Trace / Span 数据模型
- `docs/development/testing.md` —— 测试要求（前端必须 vitest + 后端必须 pytest）