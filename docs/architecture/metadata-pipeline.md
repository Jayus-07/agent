# 元数据提取管线（Metadata Pipeline）

> 文档索引流程中的元数据生成模块：分类 → 关键词提取 → 标注注入。

## 整体架构

```
config/rag.py          ← 规则配置（DOC_TYPE_RULES, FILENAME_TYPE_HINTS, DEFAULT_KEYWORDS）
       │
       ▼
rag/preprocessing/
  ├── metadata.py       ← classify_doc_type() 文档分类（加权计分 + LLM 仲裁）
  ├── keyword.py        ← 关键词提取（规则 + LLM 分流）
  └── entity.py         ← 实体提取（品牌/平台/人名/SKU）
       │
       ▼
rag/indexing/indexer.py ← _build_doc_metadata() 调度入口
```

## 完整流程

```
_build_doc_metadata(full_text, base_meta)
  │
  ├─ ① classify_doc_type(text, filename)
  │     │
  │     ├─ 加权计分：所有 DOC_TYPE_RULES 模式累计匹配得分
  │     │   - (pattern, weight) 格式，命中一次加 weight 分
  │     │   - 多次命中可累积
  │     │
  │     ├─ 文件名辅助：FILENAME_TYPE_HINTS 匹配 → +30 分
  │     │   - "退换货政策.md" → policy +30
  │     │   - "隐私合规手册.md" → compliance +30
  │     │
  │     └─ LLM 胶着仲裁：
  │         触发条件: legal/compliance/policy 中 ≥2 个类型得分差 < 5
  │         动作: 轻量级 LLM 调用，给出前三名候选，选最匹配的
  │         失败: 回退到最高分类型
  │     │
  │     └─ 返回 "faq" | "product_spec" | "policy" | "compliance" | "legal" | "general"
  │
  ├─ ② extract_doc_keywords_typed(text, doc_type) → KeywordResult
  │     │
  │     ├─ 规则关键词（所有类型都跑，零成本）
  │     │    extract_rule_keywords() → 正则 + jieba + 电商词库
  │     │    返回: [{"word": "关税", "source": "rule"}, ...]
  │     │
  │     └─ LLM 关键词（按 doc_type 分流）
  │          │
  │          ├─ policy/compliance/legal → llm_strategy="llm_force"
  │          │   强制 LLM，规则作补充
  │          │
  │          ├─ faq/product_spec → llm_strategy="rule_first"
  │          │   规则 ≥ 3 个时跳过 LLM，< 3 个补 LLM
  │          │
  │          └─ general/其他 → llm_strategy="dual_merge"
  │              规则 + LLM 双线并行，合并去重
  │     │
  │     └─ 返回: KeywordResult
  │          ├─ .rule_keywords: [{"word":..., "source":"rule"}, ...]
  │          ├─ .llm_keywords: [{"word":..., "source":"llm"}, ...]
  │          ├─ .llm_strategy: "rule_first" | "llm_force" | "dual_merge"
  │          ├─ .llm_tokens: {"prompt_tokens": N, "completion_tokens": M}
  │          └─ .all_keywords(): 合并去重后的纯字符串列表（兼容旧调用方）
  │
  ├─ ③ Span 记录（Trace 可观测）
  │      metrics:
  │        doc_type, keywords_rule(count), keywords_llm(count), keywords_total
  │        llm_prompt_tokens, llm_completion_tokens  ← 仅 llm_used=true 时
  │      output:
  │        keywords_rule: [{word, source:"rule"}, ...]
  │        keywords_llm: [{word, source:"llm"}, ...]
  │        llm_strategy: "rule_first" | "llm_force" | "dual_merge"
  │        llm_used: true/false
  │        doc_type, business_domain, person_names
  │
  └─ ④ 注入 chunk metadata
        for ch in chunks:
          ch.metadata["doc_type"] = doc_type
          ch.metadata["doc_keywords"] = ",".join(all_keywords)
          ch.metadata["person_names"] = person_names
        → embed → vector_db（ChromaDB 可按 doc_type/keywords 过滤检索）
```

## 关键设计决策

### 分流策略选择

| 文档类型 | LLM 策略 | 原因 |
|---------|---------|------|
| faq | rule_first | 术语稳定（退货/运费/保修），规则覆盖 90% |
| product_spec | rule_first | 参数格式固定，正则一把梭 |
| policy | llm_force | 语义复杂（政策类型多样），规则分不清 |
| compliance | llm_force | 法规条文需深度理解 |
| legal | llm_force | 合同条款嵌套复杂 |
| general | dual_merge | 不确定类型时全覆盖 |

### LLM 调用 Token 追踪

LLM Proxy（`backend/infra/llm/proxy.py`）在每次 `llm.invoke()` 后自动提取 `response_metadata.token_usage` 存入模块级 `_last_call_meta`。调用方在 LLM 返回后立即读取即可获取 Token 消耗。

### 关键词来源标注

新增格式 `{"word": "关键词", "source": "rule|llm"}` 替代纯字符串。前端 metadata 卡片据此渲染不同样式：
- 🔮 LLM 提取：紫色边框 Badge
- 📋 规则提取：灰色 Badge

### 向后兼容

- `extract_doc_keywords()` 保留为 `extract_doc_keywords_typed()` 的别名
- `extract_chunk_keywords()` 不变（chunk 级别仍用纯规则）
- `KeywordResult.all_keywords()` 返回纯字符串列表供旧代码使用
- 前端 `normalizeKeywords()` 兼容新旧两种格式

## 相关文件

| 文件 | 职责 |
|------|------|
| `backend/config/rag.py` | DOC_TYPE_RULES, FILENAME_TYPE_HINTS, DEFAULT_KEYWORDS, SIGNAL_RULES |
| `backend/rag/preprocessing/metadata.py` | classify_doc_type, detect_business_domain, extract_time_refs |
| `backend/rag/preprocessing/keyword.py` | extract_rule_keywords, extract_doc_keywords_typed, KeywordResult |
| `backend/rag/preprocessing/entity.py` | extract_person_names, extract_sku_codes |
| `backend/rag/indexing/indexer.py` | _build_doc_metadata（调度入口）, span 记录 |
| `backend/infra/llm/proxy.py` | LLM 调用 + Token 自动追踪 |
| `frontend/src/app/knowledge/operations/traces/[id]/page.tsx` | metadata 卡片渲染 |
