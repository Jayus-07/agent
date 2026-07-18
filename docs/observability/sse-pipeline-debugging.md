# SSE Pipeline 调试指南

> 来源：2026-07-18 多轮 bugfix 实战经验。文档按"症状 → 根因 → 修复 → 验证"组织。

---

## SSE 数据格式基础（必读）

SSE 消息有两种格式：

| 格式 | 触发哪个 listener | 适用场景 |
|------|------------------|---------|
| `event: parsing\ndata: {...}` | `eventSource.addEventListener('parsing', ...)`（**特定** listener） | 一类事件一个 listener |
| `data: {...}`（无 `event:`） | `eventSource.onmessage`（默认 message event） | 统一 listener + 解析 `data.stage` |

**前端推荐用第二种**（onmessage + 解析 data）：

```ts
eventSource.onmessage = (e) => {
  const { stage, message, doc } = JSON.parse(e.data);
  // stage 路由到 UI
};
```

---

## Bug 1: 上传后进度窗口"一闪而过"

### 症状

```
点击"上传并索引" → 进度条显示 uploading → 窗口立即关闭
```

### 根因（按可能性排序）

#### A. 前端用 `addEventListener` 注册时机 race

```ts
// ❌ 有问题
const es = new EventSource(url);  // 异步建连接
stages.forEach(s => es.addEventListener(s, ...));  // 同步注册
// ↑ 如果第一个 event 在 addEventListener 注册前到达 → 丢失
```

**修复**：用 `onmessage` 替代 `addEventListener`。

#### B. 后端 SSE 消息有 `event:` 字段但前端用 `onmessage`

```python
# 后端 _sse_encode
return f"event: {stage}\ndata: {json.dumps(data)}\n\n"
```

```ts
// 前端只设 onmessage
eventSource.onmessage = (e) => { ... };
// ↑ onmessage 只在 event 名是 'message'（默认）时触发
//   不会响应 'event: parsing' 这种自定义 event
//   → 所有 stage event 全部丢失
```

**修复**：去掉后端 `event:` 字段（让所有消息走默认 message event）。

#### C. `event_stream.pop('stage')` 把 stage 字段从 data 中抽走

```python
# 错误：抽出 stage 后 data 里没 stage
async def event_stream():
    evt = await queue.get()
    stage = evt.pop('stage', 'unknown')  # ← 抽走！
    yield _sse_encode(stage, evt)        # data 没 stage 了
```

```ts
// 前端拿到的 data: {"message": "..."}  ← 没有 stage
const stage = payload.stage || 'unknown';  // 永远是 'unknown'
if (stage === 'done') /* 永不触发 */;       // Promise 永远 pending
```

**修复**：去掉 pop，让 stage 留在 data 中。

#### D. `ensure_ascii=False` 导致非 ASCII 字节编码错误

```python
# 问题：StreamingResponse 默认 Content-Type 没 charset
return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
# 输出 UTF-8 字节 → 客户端按 latin-1 解码 → 中文乱码
```

**修复**：

```python
# ensure_ascii=True → 中文字符变 \uXXXX 转义（纯 ASCII）
# 客户端 JSON.parse 自动还原为 unicode 字符串
return f"data: {json.dumps(data, ensure_ascii=True)}\n\n"
```

#### E. `stage` 字段必须是 data 的**一部分**，不能作 SSE event 名

```python
# ✅ 正确
yield _sse_encode("message", evt)  # stage 仍保留在 evt dict 中

# ❌ 错误：抽出 stage 作为 SSE event name，但前端只用 onmessage
stage = evt.pop('stage')  # stage 从 data 消失
yield _sse_encode(stage, evt)  # data 里没 stage
```

### 验证清单

```bash
# 1. curl 后端 SSE 流，看 raw 格式
curl -N http://localhost:8000/rag/upload/<id>/stream

# 期望：
#   data: {"stage": "uploading", "message": "..."}
#   data: {"stage": "parsing", ...}
#   data: {"stage": "done", ...}
#
# 反例（有 bug）：
#   event: parsing\ndata: {...}     ← 前端 onmessage 收不到
#   data: {"message": "..."}        ← 缺 stage 字段
#   data: {"æ–‡ä»¶"}             ← 非 ASCII 乱码
```

---

## Bug 2: 前端显示"进度连接中断"

### 症状

```ts
eventSource.addEventListener('error', (e) => {
  if ((e as any).readyState === 2 && !resolved) return;  // server 已关
  cleanup();
  resolve({ ok: false, error: '进度连接中断' });          // ← 走这里
});
```

### 根因（按可能性排序）

#### A. `event_stream.pop('stage')` 让 done event 缺 stage

→ 前端 `if (stage === 'done')` 永远 false → Promise pending  
→ server 流关闭 → error 事件触发 → 走到"进度连接中断"

**修复**：见 Bug 1-C，去掉 pop。

#### B. EventSource 自动重连触发 error 事件

浏览器 EventSource 默认会在连接断开时**自动重连**（不通知 client）。  
重连过程中会触发 error 事件 + readyState=0 (CONNECTING)。

**修复**：readyState 已经是 2（CLOSED）才 skip；其他情况才 resolve。

#### C. uvicorn reload 卡住，新 worker 没启动

→ SSE 连接建立但收不到任何数据 → 触发 error → "进度连接中断"

**修复**：见 [`uvicorn-restart-recipes.md`](../operations/uvicorn-restart-recipes.md)

### 验证清单

```bash
# 1. 看 uvicorn 日志，确认最近 worker 启动了
grep "Started server process" /tmp/uvicorn.log | tail -3

# 2. 强制 kill + 干净启动（不用 reload）
pkill -f "uvicorn app.server" || true
sleep 3
nohup .venv/Scripts/python.exe -m uvicorn app.server:app --port 8000 > /tmp/uvicorn.log 2>&1 &
sleep 10
netstat -ano | findstr :8000   # Windows
```

---

## Bug 3: 重复文档"一闪而过"（看起来像成功）

### 症状

```
用户上传与已索引文档完全相同（SHA256 一致）
→ SSE 只显示 uploading + done → 窗口关闭
→ 用户以为成功，实际啥也没做
```

### 根因

`_index_file` 检测到文件 unchanged → 不跑完整索引 → 0 个 span 产生  
→ `_ProgressListener` 不触发  
→ SSE 只 emit 手动 "uploading" + 最后 "done"

### 修复（后端 + 前端）

#### 后端：检测 SHA256，emit `duplicate` stage

```python
def _do_index_sync(upload_id, filepath, filename, main_loop):
    ...
    reg = _get_registry()
    existing = reg.get_by_path(filepath)
    if existing and existing.get("status") == "active":
        with open(filepath, "rb") as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()
        if existing.get("file_hash") == file_hash:
            # 文件未变化 → 跳过完整索引
            sync_emit("duplicate", "文件已存在，未重复索引",
                      doc={**existing, "duplicate": True})
            return
    # ... 正常索引
```

#### 前端：处理 `duplicate` stage

```ts
eventSource.onmessage = (e) => {
  const stage = JSON.parse(e.data).stage;
  if (stage === 'duplicate') {
    cleanup();
    resolve({ ok: true, doc: payload.doc, duplicate: true });
  }
  // ...
};
```

```tsx
// UploadDialog
if (res.duplicate) {
  setStageMessage('文档已存在，未重复索引');
  setCurrentStage('done');
  setTimeout(() => { onSuccess(); handleClose() }, 1200);
}
```

---

## Bug 4: Windows 路径分隔符不匹配（隐藏最深）

### 症状

重复文档检测逻辑都正确，但 `existing=None`，永远走不到 duplicate 路径。

### 根因

```python
# production code
filepath = os.path.join('data/docs', 'doc.txt')
# → Windows 输出 'data/docs\\doc.txt'（混合分隔符）

# 但 db 里存的是
# → 'data\\docs\\doc.txt'（纯反斜杠） ← 早期 _scan_disk 用 os.path.join 在 root='data/docs' 时产生
```

`os.path.join('data/docs', 'doc.txt')` 在 Windows **不会**规范化已存在的 `/`，产生混合字符串。

```python
# ❌ 错
filepath = os.path.join(docs_dir, file.filename)

# ✅ 对
filepath = os.path.normpath(os.path.join(docs_dir, file.filename))
```

### 验证

```bash
D:/Python/python.exe -X utf8 -c "
import os
fp = os.path.join('data/docs', 'doc.txt')
print(repr(fp))
# Windows: 'data/docs\\\\doc.txt' (混合)
# Unix:    'data/docs/doc.txt'
print(repr(os.path.normpath(fp)))
# Windows: 'data\\\\docs\\\\doc.txt' (规范化)
"
```

---

## 完整 SSE Pipeline 数据流（修复后）

```
[后端 indexer.sync()]
    ↓
TraceCollector.end_span(index_parse)
    ↓
_trace_listener(trace, span)  ← _ProgressListener 注册的 callback
    ↓
sync_emit("parsing", "已解析 1 页")
    ↓
asyncio.run_coroutine_threadsafe(queue.put(evt), main_loop)
    ↓
[SSE event_stream]
    ↓
data: {"stage": "parsing", "message": "已解析 1 页"}\n\n  ← ensure_ascii=True
    ↓
[浏览器 EventSource]
    ↓
onmessage(e) → JSON.parse(e.data) → {stage: "parsing", ...}
    ↓
onProgress?.("parsing", "已解析 1 页")
    ↓
setCurrentStage("parsing") → React 渲染 "✓ 文本解析"
```

---

## 调试 SSE 问题的标准流程

### Step 1: 确认 uvicorn 加载了新代码

```bash
# 看是否有 reload 失败
tail -20 /tmp/uvicorn.log | grep -E "Reloading|Started|Error"
```

如果只看到 `Reloading...` 没有 `Started server process` → reload 失败，必须 force kill。

### Step 2: curl 后端 SSE 流

```bash
UPLOAD=$(curl -s -X POST http://localhost:8000/rag/upload -F "file=@test.txt")
UPLOAD_ID=$(echo "$UPLOAD" | python -c "import json,sys; print(json.load(sys.stdin)['upload_id'])")
timeout 5 curl -s -N "http://localhost:8000/rag/upload/${UPLOAD_ID}/stream" | head -10
```

**期望**：

```
data: {"stage": "uploading", ...}
data: {"stage": "parsing", ...}
data: {"stage": "chunking", ...}
data: {"stage": "embedding", ...}
data: {"stage": "writing", ...}
data: {"stage": "done", ...}
```

### Step 3: 检查反例

| 看到 | 含义 | 修复 |
|------|------|------|
| `event: parsing\ndata: {...}` | 后端用自定义 event 名 | 去掉 `event:` 前缀 |
| `data: {"message": ...}` (没 stage) | `pop('stage')` 抽走了 | 去掉 pop |
| `data: {"message": "文件"}` | ensure_ascii=True 正常 | OK |
| `data: {"message": "æ–‡ä»¶"}` | ensure_ascii=False + 无 charset | 改 ensure_ascii=True |
| 只有 uploading + done | unchanged 文档（SHA256 一致）| 添加 duplicate stage |

### Step 4: 看 Next.js 代理是否吞数据

```bash
# 绕过 Next.js 直接连后端
curl -N http://localhost:8000/rag/upload/${UPLOAD_ID}/stream

# 通过 Next.js (前端体验路径)
curl -N http://localhost:3000/api/rag/upload/${UPLOAD_ID}/stream
```

如果两边输出不一致 → Next.js rewrites proxy buffer SSE 数据。

---

## 前端 SSE 消费者模式（最终版）

```ts
async uploadDocument(file, onProgress) {
  // 1. POST 上传
  const res = await fetch('/api/rag/upload', { method: 'POST', body: fd });
  const data = await res.json();
  if (!data.upload_id) return { ok: false };

  // 2. 订阅 SSE
  return new Promise((resolve) => {
    const es = new EventSource(`/api/rag/upload/${data.upload_id}/stream`);
    let resolved = false;
    const cleanup = () => { if (!resolved) { resolved = true; es.close(); } };

    // 单一 onmessage（避免 addEventListener 注册 race）
    es.onmessage = (e) => {
      const p = JSON.parse(e.data);
      const stage = p.stage;
      onProgress?.(stage, p.message);

      if (stage === 'done') {
        cleanup();
        resolve({ ok: true, doc: p.doc });
      } else if (stage === 'duplicate') {
        cleanup();
        resolve({ ok: true, doc: p.doc, duplicate: true });
      } else if (stage === 'error') {
        cleanup();
        resolve({ ok: false, error: p.message });
      }
    };

    // Error handler：只在连接意外中断时 resolve
    es.addEventListener('error', (e) => {
      if ((e as any).readyState === 2 && !resolved) return;
      cleanup();
      resolve({ ok: false, error: '进度连接中断' });
    });
  });
}
```

---

## 关键代码位置速查

| 文件 | 行号 | 关注点 |
|------|------|--------|
| `backend/app/api/routes/rag.py` | `_sse_encode` | ensure_ascii=True，无 event: 前缀 |
| `backend/app/api/routes/rag.py` | `event_stream` | **不** pop('stage') |
| `backend/app/api/routes/rag.py` | `upload_document` | `os.path.normpath` filepath |
| `backend/app/api/routes/rag.py` | `_do_index_sync` | duplicate 检测 → emit 'duplicate' stage |
| `backend/rag/indexing/indexer.py` | `_index_file` | 6 个标准 span |
| `backend/rag/tracer.py` | `TraceCollector.subscribe()` | listener 机制（Phase 1.5） |
| `frontend/src/services/knowledge.ts` | `uploadDocument` | onmessage + duplicate stage |
| `frontend/src/components/knowledge/UploadDialog.tsx` | `handleUpload` | duplicate 分支 + handleClose 重置 |

---

## 经验教训总结

1. **SSE 数据格式有 event 名 vs 无 event 名**两种 — onmessage 只能接后者
2. **dict.pop 是破坏性读取** — 别在 SSE emit 前 pop 业务字段
3. **os.path.join 在 Windows 不规范化**已存在分隔符 — 必须 normpath
4. **uvicorn --reload 经常卡住新 worker** — 改完代码必须 force kill
5. **StreamingResponse 默认没 charset** — ensure_ascii=True 是最稳的 SSE 编码
6. **重复操作要明确告知用户** — duplicate stage 比"闪关"好
7. **前端 EventSource 不缓冲未注册 event** — 用 onmessage 更稳