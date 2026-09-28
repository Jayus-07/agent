# 测试垃圾清理清单（2026-09-28，STOP G3）

原则：磁盘上命名自证为测试产物的已直接删除；数据库内对象存在业务引用
不确定性，只列清单待确认，不做硬删。

## 已清理（磁盘，rag 数据卷 /app/data/docs）

| 路径 | 依据 |
|---|---|
| rag_eval_kb/general/real-lineage-*（含 -final、-v3 共 11 个） | Final RC lineage E2E 残留，20260920 日期戳命名 |
| rag_eval_kb/general/metadata-shadow-smoke-*(6 个) | 影子冒烟 E2E 残留 |
| rag_eval_kb/general/plain-llm-smoke-*(1 个) | LLM 冒烟 E2E 残留 |
| rag_eval_kb/general/faq-shadow-smoke-20260920111245-4cf0cb-1.md | 同上（保留未删，属同批，可下一轮一并清） |
| rag_100_docs/general/shadow-e2e-probe.md / -2.md | 本轮收口自建探针，验证完毕 |
| rag_100_docs/general/real-lineage-ocr-*.pdf（sync 报错的 2 个扫描件） | 转 rag_eval_kb 后见上 |

> 删除文件后，下次 rag-service sync 会把对应 doc_registry 行标记 deleted。

## 待确认清单（数据库，agent_memory@5433 权威库 —— 未删）

1. **doc_registry**：kb_id='..' 的 16 行（pending_review，路径即 ".."，上传路径
   解析缺陷产物）；kb_id='kb' 下 doc_a.md 等无意义名测试行。
2. **live 库测试残留表**：public.doc_registry_f2_test / doc_registry_r1_test /
   doc_registry_r4_version_test / test_rag_index_meta / test_rag_vectors
   （Final RC 测试直接打权威库时落下的影子表，确认无引用后 DROP）。
3. **ai.metadata_shadow_jobs**：修复前因投递失败滞留的 pending 行
   （05:24 批 4 条 + 07:20 批 1 条，消息已丢失永不消费）→ 建议
   UPDATE status='skipped', error='dispatch broken before 2026-09-28 fix'。
4. **auth.users** 测试账号（建议保留 e2e_budget/admin2 作回归账号，其余复核）：
   123123 / 1231231 / cs_p35_e2e / e2e_p0ae / d2_20260920 / cs_wire_plain /
   wbmuc5pmgo / e2e_authz / e2e_sqlv / e2e_sqle / p6lc*（3 个 must_change_password）。
5. **宿主机 5432 原生 PG** 上的陈旧副本库（agent_memory/demo/agent_business）：
   .env 已切 5433 后无仓库内引用，确认外部（Java 侧等）不用后可归档。

## 已知不完整统计（G4，price_unknown）

运行栈 embedding = `qwen3.7-text-embedding`（rag-service/worker 日志
`model_price_missing` 刷屏）：model_price 表无该模型条目，Token 用量页
「预估成本」不含 embedding 腿。price_per_unit 为 NOT NULL numeric，无法
表达「未知」，按纪律不编造价格——待供应商报价后在管理端
模型与供应商 → 供应商页录入，成本统计即自动补全。
