# Handover 2026-07-18 Phase 3 补漏 — Mock 模式问题

> 上一会话：Phase 3 已提交（commit be24c29）。本会话发现**查看链路显示不存在**，根因已定位，待验证。

---

## 问题：操作中心"查看链路"显示不存在

### 根因

前端默认 Mock 模式（`NEXT_PUBLIC_USE_MOCK=true`），操作日志的 trace_id（如 `82a04db3d19a`）在 Mock 数据里不存在，所以跳转后显示"Trace xxx 不存在"。

### 已修复

修改 `frontend/.env.local`：
```diff
-# NEXT_PUBLIC_USE_MOCK=true
+NEXT_PUBLIC_USE_MOCK=false
```

### 待验证

1. **重启前端**：
   ```bash
   cd frontend
   npx next dev
   ```

2. **浏览器验证**：
   - 访问 `/knowledge/operations`
   - 点击任意"查看链路"
   - 确认显示 6 span（parse/chunk/embed/vector_db/metadata）

3. **后端确认 trace 存在**：
   ```bash
   curl -s "http://localhost:8000/observability/traces/{trace_id}" | python -m json.tool
   ```

---

## 额外需求（待做）：批量上传展示

### 当前问题

批量上传 10 个文件 = 操作中心 10 条 upload 记录，列表臃肿。

### 推荐方案（P2）：加批次 ID

**后端改动**：
```python
# backend/app/api/routes/rag.py:upload_document
batch_id = request.query_params.get('batch_id') or str(uuid.uuid4())
_safe_log_op('upload', ..., detail={'batch_id': batch_id})
```

**前端改动**：
```typescript
// frontend/src/services/knowledge.ts
async uploadDocuments(
  files: File[],
  onPerFileProgress?: (file: File, stage: string, message: string) => void,
  batchId?: string,
): Promise<{ ok: number; failed: { name: string; error: string }[] }> {
  const id = batchId || crypto.randomUUID()
  for (const file of files) {
    // SSE progress + batch_id query param
  }
}
```

**操作中心页面加"批次"列**，点击按批次聚合。

---

## 服务状态

- 后端：running（端口 8000）
- 前端：需重启（修改了 .env.local）

## 分支

- `feat/knowledge-batch-ops`（已提交 Phase 3）
- .env.local 改动**未提交**（用户本地配置）

## 验证后需 commit

```bash
git add frontend/.env.local
git commit -m "fix(observability): 禁用 Mock 模式，链路追踪对接真实 API"
```