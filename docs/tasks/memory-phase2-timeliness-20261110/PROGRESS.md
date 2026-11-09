# PROGRESS: 记忆第 2 期——时效

## 2026-11-10 S0 开工前整体链路评审（已完成）

评审基准：main = e08b868（#415 已合并），逐文件读当前代码。

### 链路现状全景（第 1 期落地后）

| 链路 | 现状（文件:证据） |
|---|---|
| 写链 | end_turn → MemoryExtractionJob outbox（product+source_trace_id）→ worker → service.store → conflict |
| 裁决链 | conflict.resolve_keyed：裁决矩阵 explicit>inferred>legacy（_resolve_conflict 163-199）；supersede 三步同事务（deactivate→insert→supersede 回填 superseded_by）；resolve_unkeyed 只判 duplicate 永不 supersede |
| 召回链 | search_hybrid SQL hard eligibility（models/memory.py + memory_repo.py:45-89）：is_active+tenant+product+user+verification(verified/legacy)+scope+expire_at 未过期 → retriever（gate→global 分流→rank 0.5sim+0.3imp+0.2recency→merge→max-K） |
| 画像链 | list_active_profile / list_pending_profile：同 eligibility 无向量（memory_repo.py:122-168） |
| 衰减链 | beat 04:30 memory-daily-decay（celery_app.py:168）→ memory_maintenance_tasks.py:34 → MemoryDecayService.run（decay.py）：两档 importance 衰减（180d ×0.9 / 90-180d ×0.95）+ archive_stale(<0.2→is_active=False) + explicit 全豁免 |
| outbox 恢复 | memory.extraction_recovery 15s 扫描（durable 投递失败兜底） |
| trace | 请求级 trace + runner 创建 memory.retrieve span（service 无 ambient trace 时 noop）；任务级 root trace **尚无先例**（S5 需查 trace_collector 建独立 trace 模式） |

### 第 2 期增量与既有事实的映射

- D3"evidence 已有"：source_message_id 列 + D9 source_trace_id 已落地，**无需新 evidence 表**
- D3 增量 = valid_from/invalid_at 两列；失效仍走 is_active=False + invalid_at 双写
  （uq_memory_active_key 是 partial WHERE is_active——若失效行保留 is_active=True
  会阻止同 key 新行插入，故必须双写，唯一索引不动）
- D4 矛盾失效 / 碎片合并：均可用 supersede 版本链承载（is_active=False +
  superseded_by=聚合行），天然可审计可回滚 → D-4 推荐方案的依据
- D4 边界冲突：archive_stale（is_active=False=隐藏）违反"不删除不隐藏"→ D-1
- LLM 预算：dreaming 若调 LLM 需归因 memory-dreaming:<trace_id>（对齐第 1 期
  memory-extract:<job_id> 模式）

### 待裁决

D-1 archive_stale 去留 / D-2 安全豁免判定 / D-3 配置形态 / D-4 合并实现——
已附推荐方案（见 TASK.md），等用户裁决后开工 S1。

## 2026-11-10 #415 合并后 rebuild 补验（完成，UNVERIFIED 解除）

- main = e08b868，主仓 devctl rebuild：backend 全栈 Up (healthy)
- 容器 agent-app-1 Image ID = agent-app:latest（45cc9b0b1ed7）一致，镜像内
  含 #415 两个修复（tools/memory.py `_resolve_memory_identity` ×2、
  service.py legacy digest ×1）——修复已在本机栈实际运行
- frontend 容器未重建（本次改动无前端）；frontend 阶段 PGPASSWORD 插值报错
  根因待查（与后端无关，backend 阶段同配置插值成功），遗留观察项
- 并行会话竞态：本次 rebuild 未被覆盖；竞态风险仍在（17 个 worktree 共享
  compose 项目 agent），后续会话操作栈时注意核对容器 Image ID

## 2026-11-10 S1 前置链路评审（已完成）

评审基准：main = e08b868，worktree agent-wt-mem-phase2（分支 feat/memory-phase2-timeliness-20261110）。

| 评审项 | 结论 |
|---|---|
| 时间列时区形态 | memory_records 三列均为 `timestamp without time zone`（ORM 声明 timezone=True 与实际列不符是既有漂移，写入按 UTC 约定）——新列保持 `TIMESTAMP WITHOUT TIME ZONE`，DEFAULT 显式 `NOW() AT TIME ZONE 'UTC'` 防会话时区漂移 |
| 迁移编号 | **144 已被 budget-batch1 占用**：共享库 schema_migrations 已有 `144_budget_reservation_resolution.sql` 记录（其文件未提交 git、库比 main 领先）→ 本期用 **145**，内容（加列）与 144 无依赖；init_db 对账方向是"磁盘文件→已应用跳过"，磁盘缺 144 文件不会报错 |
| 唯一键兼容 | uq_memory_active_key partial WHERE is_active 不动：失效 = is_active=False + invalid_at **双写**（失效行保留 is_active=True 会阻止同 key 新行插入） |
| init_db 登记 | `"145_memory_time_validity.sql": "memory"`（143 同形态，init_db.py:168） |
| 注册测试 | test_migration_registration.py 追加 145 断言（session_tenant < product_scope < time_validity 顺序） |
| CI 分类 | NON_AI_MIGRATION_ENTRIES 追加 145（143 同注释口径：表结构迁移，不改变模型调用面） |
| 索引 | 不加新索引：召回过滤 invalid_at 是行级条件（走现有 idx_memory_scope，千级数据量）；审计查询低频 |
| valid_from 语义 | NOT NULL；存量回填 created_at；新行 insert 时写入；REAFFIRMED 强化不改 valid_from |

## 2026-11-10 S1 施工与验证（完成）

改动（worktree agent-wt-mem-phase2，5 文件）：
1. 新增 `backend/sql/migrations/145_memory_time_validity.sql`：valid_from（NOT NULL，
   DEFAULT UTC NOW）+ invalid_at（nullable），存量回填 valid_from=created_at
2. `backend/memory/models/memory.py`：MemoryRecord 两列 ORM 声明
3. `scripts/init_db.py`：MIGRATION_TARGETS 登记 145 → memory
4. `.github/scripts/rag_smoke_path_filter.py`：NON_AI_MIGRATION_ENTRIES 追加 145
5. `backend/tests/memory/test_migration_registration.py`：145 注册与顺序断言

验证：
- 本机库应用 145：906 行全部回填 valid_from（total=has_vf=906）、invalid_at 全 NULL
- 列形态：valid_from NOT NULL+default ✅、invalid_at nullable ✅、timestamp without time zone
- 幂等：init_db 重跑 145 skipped「已应用（记录一致）」rc=0
- 容器回归面：**167 passed**（含 145 注册断言），与基线持平
- git：分支内 commit（整期完成后统一建 PR）

## 阻塞与下一步

- 阻塞：无（S0 评审完成）
- 下一步：用户裁决 D-1~D-4 → 建 worktree → S1 迁移（前置链路评审→施工→验证）
