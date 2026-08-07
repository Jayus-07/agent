"""评测集适配层 — 让 runner 支持多版本数据集。

原始 datasets/rag.json 标注了 6 个 KB（AMAZON_SOP/CUSTOMER_SERVICE/POLICY/...）
但实际 doc_db 只有 policy_general KB 的 2 个文档，golden set 标注的 doc_id (KD0001)
与实际 UUID 格式 doc_id 不匹配，导致 baseline 召回率永远 0。

datasets/rag_v2.json 是修复版：
- 7 条基于 doc_db 真实文档（客服综合业务规范、跨境电商合规规范）
- doc_id 用真实 UUID (62a44c7dd7cfc933, d5fdcafa0b404f61)
- 全部 kb_id=policy_general
- 评测能反映真实检索能力
"""
