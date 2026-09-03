# Prompt Management API

Base path: `/api/prompts`

所有端点通过 `X-Operator-Role` header 进行权限控制（默认 `viewer`）。

## Endpoints

### GET /prompts

列出所有 prompt，支持过滤。

**Query params:**
- `category` (optional) — 按分类过滤
- `risk_level` (optional) — 按风险等级过滤
- `q` (optional) — 关键词搜索

**Response:**
```json
{
  "items": [
    {
      "id": "uuid",
      "key": "rag.qa",
      "name": "RAG 问答生成 Prompt",
      "category": "rag",
      "risk_level": "medium",
      "variables": [{"name": "context"}, {"name": "input"}],
      "variable_count": 2,
      "active_version": 3,
      "is_code_controlled": false,
      "created_at": "2026-09-01T00:00:00",
      "updated_at": "2026-09-03T00:00:00"
    }
  ],
  "total": 36
}
```

---

### GET /prompts/meta/registry

返回静态注册表元数据（不查 DB）。

**Response:**
```json
{
  "specs": [
    {
      "key": "rag.qa",
      "name": "RAG 问答生成 Prompt",
      "category": "rag",
      "risk_level": "medium",
      "variables": [
        {"name": "context", "required": true, "description": ""}
      ],
      "code_controlled": false,
      "required_substrings": ["<!--META"],
      "default_file": "rag_qa.yaml"
    }
  ],
  "total": 36
}
```

---

### GET /prompts/{key}

获取单个 prompt 详情 + active template。

**Headers:** `X-Operator-Role`

**Response (DB source):**
```json
{
  "key": "rag.qa",
  "name": "RAG 问答生成 Prompt",
  "category": "rag",
  "risk_level": "medium",
  "variables": [...],
  "active_version": 3,
  "source": "db",
  "template": "<!--META--> 基于以下上下文...",
  "code_controlled": false,
  "required_substrings": ["<!--META"]
}
```

**Response (default fallback, DB 无记录时):**
```json
{
  "key": "rag.qa",
  "source": "default",
  "template": "...",
  "active_version": null
}
```

---

### GET /prompts/{key}/versions

列出指定 prompt 的所有版本。

**Response:**
```json
{
  "items": [
    {
      "id": 10,
      "version": 3,
      "template": "...",
      "variables": ["context", "input"],
      "status": "published",
      "change_note": "Updated context format",
      "created_by": "admin",
      "created_at": "2026-09-03T00:00:00"
    }
  ],
  "total": 3
}
```

---

### GET /prompts/{key}/versions/{version}

获取单个版本详情。

---

### GET /prompts/{key}/diff

生成两个版本间的 unified diff。

**Query params:**
- `from_version` (required) — 起始版本号
- `to_version` (required) — 目标版本号

**Response:**
```json
{
  "key": "rag.qa",
  "from_version": 2,
  "to_version": 3,
  "diff": "--- rag.qa v2\n+++ rag.qa v3\n@@ -1,3 +1,4 ...\n"
}
```

**注意:** code-controlled prompt 不允许 diff（返回 403）。

---

### POST /prompts/{key}/drafts

创建新版本草稿。

**Headers:** `X-Operator-Role: editor`, `X-Operator-Id: <name>`

**Body:**
```json
{
  "template": "新的模板文本 {context} {input}",
  "change_note": "修改了上下文格式"
}
```

**Response:**
```json
{
  "version": 4,
  "status": "draft"
}
```

---

### POST /prompts/{key}/publish

发布指定版本。

**Headers:** `X-Operator-Role: admin`（high risk），`editor`（medium/low）

**Body:**
```json
{
  "version": 4
}
```

**Response:**
```json
{
  "active_version": 4,
  "previous_version": 3
}
```

**副作用:**
- 清除 cache
- 更新 in-process snapshot
- 递增 epoch
- 写入审计日志
- 触发 reload hooks

---

### POST /prompts/{key}/rollback

回滚到指定版本（内部等同于 publish）。

**Body:**
```json
{
  "version": 2
}
```

---

### POST /prompts/{key}/render

干跑渲染 — 返回渲染结果但不调用 LLM。

**Body:**
```json
{
  "variables": {"context": "...", "input": "问题"},
  "template": null
}
```

- `template` 为 null 时使用当前 active 版本
- `template` 非 null 时使用提供的模板（playground 预览模式）

**Response:**
```json
{
  "key": "rag.qa",
  "version": 3,
  "source": "snapshot",
  "text": "渲染后的完整文本",
  "text_length": 256
}
```

---

### POST /prompts/{key}/playground

渲染 + LLM 调用。

**Body:**
```json
{
  "variables": {"context": "...", "input": "问题"},
  "template": null,
  "model": null
}
```

- `model` 为 null 时使用默认 LLM

**Response (success):**
```json
{
  "rendered": {
    "text": "渲染后的模板文本",
    "key": "rag.qa",
    "version": 3,
    "source": "snapshot"
  },
  "llm_output": "LLM 生成的回复",
  "latency_ms": 1234
}
```

**Response (LLM failed):**
```json
{
  "rendered": {...},
  "llm_output": null,
  "llm_error": "Connection timeout",
  "latency_ms": null
}
```

---

### GET /prompts/{key}/audit

获取审计日志。

**Query params:**
- `limit` (optional, default 50)

**Response:**
```json
{
  "items": [
    {
      "id": 1,
      "prompt_key": "rag.qa",
      "action": "publish",
      "from_version": 2,
      "to_version": 3,
      "actor": "admin",
      "role": "admin",
      "detail": {},
      "created_at": "2026-09-03T00:00:00"
    }
  ],
  "total": 5
}
```

---

### POST /prompts/seed

从 YAML defaults 种子化 DB（幂等操作）。

**Headers:** `X-Operator-Role: admin`

**Body:**
```json
{
  "auto_seed": true
}
```

**Response:**
```json
{
  "seeded": 35
}
```

仅对 DB 中不存在且非 code-controlled 的 prompt 创建初始版本。

## Error Codes

| Code | Meaning |
|------|---------|
| 403 | 权限不足 / code-controlled prompt 尝试修改 |
| 404 | Prompt key 不存在 / 版本不存在 |
| 422 | 模板验证失败 / 变量不匹配 |

## Frontend Service

前端通过 `frontend/src/services/prompts.ts` 封装 API 调用：

```typescript
promptsService.list()           // GET /prompts → .items
promptsService.get(key)         // GET /prompts/{key}
promptsService.registry()       // GET /prompts/meta/registry → .specs
promptsService.versions(key)    // GET /prompts/{key}/versions → .items
promptsService.diff(key, f, t)  // GET /prompts/{key}/diff
promptsService.draft(key, tpl)  // POST /prompts/{key}/drafts
promptsService.publish(key, v)  // POST /prompts/{key}/publish
promptsService.rollback(key, v) // POST /prompts/{key}/rollback
promptsService.render(key, v)   // POST /prompts/{key}/render
promptsService.playground(key, b) // POST /prompts/{key}/playground
promptsService.audit(key)       // GET /prompts/{key}/audit → .items
```
