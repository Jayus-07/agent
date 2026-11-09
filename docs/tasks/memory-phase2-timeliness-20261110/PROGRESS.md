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

## 2026-11-10 S2~S6 前置链路评审记录（施工后补记，核对动作均在施工前完成）

> 说明：S2~S6 的链路核对实际在各自施工前完成（会话中逐项 grep/Read），
> 但未按 TASK.md 纪律即时落盘，此处按施工前真实核对结果补记。教训：
> 评审结论必须当步落盘，不能依赖会话记忆。

### S2（supersede 失效双写）前置评审

- `grep deactivate/supersede 调用方`（backend/memory+tools+tasks）：唯一
  调用方 = conflict._resolve_conflict:188（deactivate）/194（supersede 回填）
  ——影响面完全可控
- tombstone 行不写 invalid_at：删除屏障非事实失效，语义区分靠
  memory_type；tombstone 本就 is_active=False 不进召回
- REAFFIRMED/DUPLICATE/INSERTED 路径不动（invalid_at 保持 NULL）
- new 行 valid_from 走 ORM default（_build_record 不动，最小改动）

### S3（召回过滤）前置评审

- 基于 S0 全景的五路径 WHERE 定位 + 施工前重读 worktree 文件确认行级内容
- `find_active_by_key` 不加过滤：supersede 定位靠 is_active，保持定位
  逻辑纯净
- 过滤形态对齐现有 expire_at（text("NOW()")，timestamp 列按 UTC 会话约定）
- 防御性口径说明：is_active=False 已挡现行失效路径，invalid_at 条件
  统一未来失效来源（矛盾失效/合并——S5 将引入）

### S4（移除归档）前置评审

- `grep archive_stale/run_decay/"archived" 消费方`：archive_stale 唯一
  调用方 = decay.run:27；run_decay 唯一消费方 = memory_maintenance_tasks
  （run_on_worker_loop 包装）；evaluation 域的 "archived" 是 dataset case
  归档，与 decay 无关
- 契约变更识别：STOP C「<0.2 归档」为冻结契约，移除属契约变更——
  decay.py/任务 docstring/memory-governance.md 三处明示
- metric 评估：memory_decay_records_total 的 archived 序列自然消失
  （Counter 定义保留），Grafana 面板空序列无害

### S5（dreaming）前置评审

- trace：TraceCollector.start/finish API + finish 持久化确认
  （tracer.py:601 finish → trace_writer 队列 → SQLite，后台任务可落库）；
  WorkflowKind 枚举无 memory 值 → workflow_name=memory_dreaming +
  root span type="memory"（D9 口径）
- 预算归因：scoped_request_budget（budget.py:405）+ memory-extract:<job>
  先例（extraction_tasks.py:338）→ memory-dreaming:<tenant>:<user>
- beat 样板：eval_sampling_tasks.py 薄壳 + skipped 正常终态模式；
  decay beat 登记（celery_app.py:168）+ queue_router beat_binding 形态
- LLM 接口：extract_facts 的 llm.invoke + to_thread 模式（long_term.py:141）
  → dreaming._call_llm 封装便于测试 stub
- config 归属：backend/config/memory.py（env 驱动常量段）
- 共享库风险：dreaming.run 全量扫描在测试共享库上会对真实用户执行
  动作 → 增加 run(user_scope) 参数（测试/手动重放限定单用户）

### S6（产品档位）前置评审

- apply_decay SQL 现状（全表跑、两档互斥、explicit 豁免）在 S0/S4 已核
- D4 安全注入要求推导：importance 进 rank 0.3 权重 → 降权可能挤出
  top-K → 安全记忆「永远参与自动注入」要求豁免衰减（必要条件）
- 豁免实现双通道：keyed 行 SQL NOT LIKE 前缀（AND 语义）+ unkeyed 行
  内容关键词参数化 LIKE 排除（SQL 无法走 Python 白名单函数，参数化拼装）
- 消费方核对：decay.run 唯一调用方（S4 已核）+ 返回结构 by_product
  新增字段（任务包装 log 兼容）

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
