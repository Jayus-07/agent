# RAG 上传链路故障模式分析（FMEA）— 2026-09-17

> 范围：`POST /api/rag/upload` → 落盘 → Celery/本机索引 → 五路存储 → SSE 终态。
> 用途：评审"上传链路验收清单"的整改依据；每次动上传链路前过一遍本表。

## 1. 故障模式与现有防护

| # | 故障模式 | 影响 | 现有防护 | 残余风险 | 状态 |
|---|---------|------|---------|---------|------|
| F1 | 落盘中途失败/超限 | tmp 孤儿、磁盘泄漏 | 流式写 + 双保险限流 + 必删 tmp（F1 修复） | 极端:删除也失败 | ✅ 已防护 |
| F2 | 同文件并发上传 | 同 doc_id 双写向量库 | `.lock` 原子锁 + 心跳 60s + stale 30min 接管 | 锁文件系统依赖 | ✅ 已防护 |
| F3 | Celery 模糊投递（broker 已接收但响应丢失）→ 本机回退，双执行体 | 双索引/误删源文件 | 文件锁串行化；输家收敛 duplicate；`_run_index_background` 锁冲突分支不收口不删文件（bdaded7） | 跨上传锁冲突时 SSE 等 1900s 超时兜底 | ✅ 已防护 |
| F4 | 重索引中途失败（vdb/registry 阶段） | 旧版本连带删除（`_remove_document` 按 doc_id 条件删） | parse/chunk/embed 阶段失败旧数据保留；Sweeper 兜底 | **vdb 阶段失败仍丢旧版本**（代码注释自认 indexer.py:1127） | ⚠️ 待改：按本次写入 chunk_ids 精确删 |
| F5 | BM25 replace_documents 失败被吞 | 向量库有、BM25 无 → 混合检索缺一半 | Sweeper `_check_bm25` 缺失检测（active 口径）+ chunk_store 重建（bdaded7） | 检测周期内（最长 6h）检索降级 | ✅ 已防护 |
| F6 | Worker 中途杀死/重启 | 占位行悬挂 | `INTERRUPTED_STATUSES` 启动恢复：文件在→重索引；丢失→deleted；失败→failed（`_recover_interrupted`） | 恢复依赖 sync() 被触发 | ✅ 已防护（test_indexer_recovery_and_reindex.py） |
| F7 | 进度镜像 TTL 过期（任务队列滞留 >10min） | SSE 误报"已过期" | 入队即写镜像缓解冷启动；1900s 轮询超时兜底 | 任务排队 >31min 仍误报 | ⚠️ 状态机改造时一并修（CREATED/QUEUED/RUNNING） |
| F8 | 多实例部署，SSE 订阅打到非上传实例 | 进度丢失 | 无（`_progress_queues`/`_celery_routed` 进程内） | 单机部署无影响 | ⚠️ 扩容前必须 Redis 化 |
| F9 | 覆盖上传索引失败 | 用户丢旧版本 | `.bak` 保留 + 失败不删源 + 成功才清 .bak | 人工恢复 | ✅ 已防护 |
| F10 | registry/答案缓存不一致 | 旧答案返回 | 成功终态 invalidate_kb | 失败路径缓存保留（正确） | ✅ 已防护 |

## 2. 告警与验收

- **打点**：`rag_consistency_issues_total{store,severity}`（检出）、`rag_consistency_repairs_total{store,result}`（修复）。error 级持续增长 → Alertmanager 告警。
- **验收测试**：`test_rag_upload_*`（6 文件 103 用例）+ `test_indexer_recovery_and_reindex.py`（Worker 中断恢复）+ `test_upload_resilience.py`（Sweeper/锁/预检）。
- **评审清单 7 项映射**：1/2/3/6 ✅ 已覆盖；4/5 本轮补齐（celery_mode TestExecuteIndexTaskImpl/TestRedisPollEvents）；7 = F8，多实例改造后补。

## 3. 遗留整改批次（按优先级）

1. **F4**：重索引 vdb 阶段失败改为按本次写入的 chunk_ids 精确删（半天）。
2. **F7+F8**：上传会话状态 Redis 化（CREATED/QUEUED/RUNNING/终态 + 心跳），SSE 纯订阅（独立批次，扩容前置）。
3. **F3 残余**：跨上传锁冲突的 SSE 等待体验（可选：明确报 file_locked 而非等超时）。
