"""selection — 智能选品引擎

scoring:      规则加权评分（纯函数）
market_index: 快照 → Chroma competitor_market collection（语义趋势）
trends:       快照 SQL 聚合（结构趋势）
recommender:  编排层（打分 + LLM 理由 + 组装）
store:        SelectionStore（评分缓存 / 权重配置）
"""
