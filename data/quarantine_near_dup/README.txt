近重复副本隔离区（2026-09-17 R3 会话）。
这 4 份文件与主语料 active 文档 sim>=0.85 近似重复，基线状态为 pending_review
（检索层按状态软过滤）。因 LLM 分类漂移导致 near-dup 检测跨 doc_type 失效、
每次启动 sync 会把 pending 行重新翻成 active，故移出 data/docs 以固定基线。
接入在线 OCR/重审后可人工裁决回归。