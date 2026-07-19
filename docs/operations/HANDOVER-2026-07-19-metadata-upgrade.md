# Handover 2026-07-19 — Metadata Pipeline 企业级升级

> 分支: `feat/knowledge-batch-ops` · 已提交 · 约 600 行改动

## 一、改动总览

### 提交链

```
66d2e66 P0: 分类标题加权 + 低置信LLM复验 + 摘要大小阈值
14ffc95 P1: LLM合并 + 质量门禁 + 元数据查询API + 嵌入版本 + Entity升级
1060a43 P2: MinHash语义去重 + Chunk Qwen批量调用
```

### 改动文件

| 文件 | 改动 |
|------|------|
| `backend/rag/indexing/indexer.py` | `_build_doc_metadata()` 重构，加 quality/entities/minhash/merged LLM |
| `backend/rag/preprocessing/metadata.py` | 加 `assess_quality()`、`enrich_metadata_llm()`、`compute_minhash()`、标题关键词加权 |
| `backend/rag/preprocessing/keyword.py` | 加 `extract_chunk_keywords_qwen_batch()`、关键词兜底 `llm_fallback` |
| `backend/rag/preprocessing/entity.py` | 重写：jieba NER 替换纯正则，5 类结构化实体 |
| `backend/rag/indexing/doc_registry.py` | 加 `quality_score/issues`、`embedding_model`、`minhash_sig`、`near_dup_id` 列 + `list_by_doc_type()` + `search()` 扩展 |
| `backend/rag/indexing/chunk_store.py` | 加 `section_title`、`llm_keywords`、`llm_model` 列 |
| `backend/infra/llm/proxy.py` | `_record_tokens` 支持 `input_tokens`/`output_tokens` 字段名，改用 `clear()+update()` 避免 import 引用陷阱 |
| `backend/app/api/routes/rag.py` | API 加 `doc_type`/`confidence_min`/`llm_used`/`quality_min`/`sort_by` 参数；chunk detail API + section_title |
| `backend/app/api/routes/observability.py` | trace DTO 加 `tags` 字段 |
| `backend/config/rag.py` | 加 `DOC_LLM_MODEL`、`CHUNK_LLM_MODEL` 配置 |
| `frontend/src/app/knowledge/operations/traces/[id]/page.tsx` | trace 详情页大幅重写（见下方） |
| `frontend/src/components/knowledge/UploadDialog.tsx` | `flushSync` 进度 + 上传成功后自动跳转 trace |
| `frontend/src/services/knowledge.ts` | `getChunkDetail()`、`batchDelete`/`batchReindex` 加 `batch_id` |

---

## 二、Metadata Pipeline 最终状态

### 文档级（`_build_doc_metadata` 9 步）

```
① assess_quality()         → {score, passed, issues}
② classify_with_confidence() → (doc_type, confidence)
     · 正则加权 + 文件名 + 文件夹路径 + 标题关键词
     · confidence<0.3 + general → LLM 复验
③ compute_minhash()        → 128 签名 → 同类型去重检查
④ extract_time_refs()      → 日期/月份字符串列表
⑤ detect_business_domain() → "product" | "general"
⑥ extract_rule_keywords()  → 规则关键词（传数量给⑦）
⑦ analyze_complexity()     → {headings, table_rows, legal_refs, risk_keywords...}
⑧ extract_doc_keywords_typed() → KeywordResult（LLM Router 分流）
     · llm_force: policy/compliance/legal/security/financial
     · rule_first: faq/product_spec/listing/sop（≥50分调LLM）
     · dual_merge: 其他（≥50分调LLM）
     · fallback: 规则<3个时强制LLM
⑨ enrich_metadata_llm()    → 合并调用：keywords+summary+entities（替代2次独立调用）
     · len(text)>=2KB → LLM summary
     · len(text)<2KB  → 提取式 summary（不调LLM）
```

### Chunk 级

```
每个 chunk:
├─ 规则关键词 (extract_rule_keywords)
├─ section_title (章节位置映射)
└─ high-value 文档 → Qwen 批量调用（1次prompt处理所有chunk）
```

### LLM 调用点汇总

| 调用点 | 配置 | 触发条件 |
|--------|------|---------|
| 文档关键词+摘要+实体 | `DOC_LLM_MODEL` 分流（空=DeepSeek） | 高价值/大文档/低置信 |
| 低置信分类复验 | `DOC_LLM_MODEL` 分流 | confidence<0.3 + general |
| Chunk 关键词 | `CHUNK_LLM_MODEL`=qwen2.5:3b | 高价值文档，批量调用 |
| 胶着仲裁 | `_LLMProxy` | legal/compliance/policy 分数接近 |

### 配置项

```bash
# .env
DOC_LLM_MODEL=qwen2.5:3b    # 文档级用本地（免费），不设走 DeepSeek
CHUNK_LLM_MODEL=qwen2.5:3b  # Chunk级用本地（默认）
```

---

## 三、Trace 详情页前端结构

```
📦 处理流水线
├─ 文件加载      (折叠行)
├─ 解析文档      (折叠行)  
├─ 数据清洗      (展开卡片: 清洗前后字符对比 / 错误信息)
├─ 去重检查      (展开行: 命中缓存/进入索引)
├─ 文本分块      (展开卡片: 预览块 + 📋查看完整内容按钮)
│   └─ 侧边面板: Chunk全文 + 字 + Qwen关键词 + 规则关键词 + 章节路径
├─ 元数据生成    (默认折叠, 点击展开)
│   ├─ 📝 摘要 (首行)
│   ├─ 🔮 LLM提取(文档级) + Token/花费/模型名
│   ├─ 📋 规则提取(文档级)
│   └─ 🔧 技术详情(可折叠)
│       ├─ 💡 决策原因 (中文翻译)
│       ├─ 📊 文档结构
│       ├─ 🕐 时间引用
│       └─ 🏷️ 实体
├─ 向量嵌入      (展开行: 成功/失败计数 + 模型名靠右)
└─ 写入向量库    (折叠行)
```

---

## 四、数据存储全景

| 存储 | 内容 |
|------|------|
| `data/docs/` | 原始文件 |
| `CHROMA_PATH` (ChromaDB) | chunk 向量 + metadata(doc_type, person_names, chunk_keywords) |
| `DOC_DB_PATH` (ChromaDB) | doc 全文向量 + doc 级元数据 |
| `data/doc_registry.db` (SQLite) | 文档注册表(25列): file_hash, doc_type, confidence, llm_used, quality_score, quality_issues, embedding_model, minhash_sig, near_dup_id, status |
| `data/chunk_store.db` (SQLite) | chunk 文本(8列): content, keywords, llm_keywords, llm_model, section_title |
| `data/doc_operation_log.db` (SQLite) | 操作审计日志 |
| `data/keyword_rules.db` (SQLite) | 关键词规则动态管理 |
| TraceCollector (内存) | trace 记录(max 200) — 重启丢失 |

---

## 五、已知未完成

| 项目 | 优先级 |
|------|--------|
| Chunk role 标注（定义/流程/表格） | P2 |
| PostgreSQL JSONB 迁移 | P2 |
| Trace 持久化到 DB | P2 |
| 跨文档关系图谱 | P2 |
| 权限与密级 | P2 |

---

## 六、验证方法

1. 后端需要重启（uvicorn --reload 或 `restart_all.bat`）
2. 浏览器上传一个合规/财务类文档 → 查看 trace 详情页
3. 检查元数据卡片：摘要 + LLM关键词（含Token花费模型名） + 规则关键词 + 技术详情
4. 检查 Chunk 面板：Qwen关键词 + 规则关键词 + 章节路径
5. 上传重复文档：上传提示"已存在（内容未变更，跳过索引）"
6. API 测试: `GET /rag/documents?doc_type=compliance&confidence_min=0.5`
7. CLI 测试: `GET /rag/chunks/{doc_id}/detail` 返回完整 chunk 文本
