# TASK: 记忆第 2 期——时效（D3 时间有效性 + D4 dreaming 整合 + D5 衰减产品配置）

- 创建：2026-11-10；前置：第 1 期（#413）+ 评审修复（#415）已合并，main = e08b868
- 设计依据：[ADR-0012](../../architecture/adr/0012-memory-platform-product-namespace.md) D3/D4/D5（D4 衰减边界、D6 证据门不在本期）
- 验收（ADR 分期表）：① 过期/失效事实不召回且可审计还原 ② 记忆量有界 ③ 整合动作可回滚

## 施工纪律（用户要求，每步强制）

**每个步骤动手前，先评审该步骤涉及的当前整体链路**（读最新代码为准，
不凭记忆），把评审结论记入 PROGRESS.md 对应小节后再开工。链路评审最少覆盖：
该步改动点在 读链路/写链路/裁决链/召回链/画像链/衰减链/outbox/trace 中的
位置、影响面、与既有契约（STOP 系列冻结契约）的冲突。

## 范围（交接 3.3 + ADR 分期路线第 2 期）

1. 迁移：memory_records 加 `valid_from` / `invalid_at`（evidence 引用已有 = source_message_id）
2. conflict.py：supersede 语义扩展"旧值标记失效"（保留 origin priority/confidence 裁决矩阵）
3. 检索召回过滤 `invalid_at`（失效不召回）
4. dreaming 整合任务（复用 outbox/beat 基建）：碎片合并 / 置信度强化与衰减 / 矛盾触发失效；任务级 root trace（type=memory，动作计数；原文/PII 红线不变）
5. 衰减按产品配置（D5 表：agent 慢 / customer_service 中 / travel 快）

## 步骤拆解

| 步骤 | 内容 | 前置链路评审重点 |
|---|---|---|
| S0 | 开工前整体链路评审（本次） | 全景：写链/裁决链/召回链/画像链/衰减链/outbox/trace 现状 |
| S1 | 迁移 145：valid_from/invalid_at 两列 + 索引；ORM 同步；init_db 登记 | 迁移登记机制、143 现状、幂等、唯一键（uq_memory_active_key partial WHERE is_active）不受影响 |
| S2 | conflict supersede 扩展：deactivate 时双写 invalid_at；首建写 valid_from | supersede 三步（deactivate→insert→supersede 回填）同事务契约、REAFFIRMED 路径 |
| S3 | 召回过滤：search_hybrid / find_similar_candidates / list_active_profile / list_pending_profile 加 invalid_at/valid_from 过滤 | 五条召回路径现状、与 expire_at 过滤同位置、防御性（失效行 is_active=False 已挡） |
| S4 | decay 对齐 D4 边界（决策点 D-1） | archive_stale 冻结契约（STOP C）与 D4"不删除不隐藏"冲突 |
| S5 | dreaming 任务：beat 注册 + MemoryDreamingService + 安全豁免（决策点 D-2）+ trace root + 预算归因 | beat 模式（memory-daily-decay 样板）、outbox 恢复、trace_collector root trace 创建、LLM 预算归因 |
| S6 | 衰减产品配置（决策点 D-3） | config 常量起步 vs DB 热更 |
| S7 | 测试（双产品对照 + 失效/召回/回滚边界）+ 文档同步（memory-governance/ADR 分期勾选）+ rag_smoke 登记 | 全链回归面、167 基线 |

## 设计决策点（开工前需裁决）

- **D-1 archive_stale 与 D4 边界冲突**：现有 decay 把 importance<0.2 的行
  is_active=False（隐藏，设置页不可见）——违反 D4"衰减只降排序权重，不删除
  不隐藏"。推荐：移除自动归档，衰减只降权（importance 下限 clamp）；记忆量
  有界改由 dreaming 合并 + invalid_at 失效 + 用户删除承担。
- **D-2 安全类豁免判定**：ADR 说健康/安全类事实豁免衰减。推荐：memory_key
  前缀白名单（health.* / safety.* / dietary.* / emergency.*）写入时由
  ImportanceScorer 提权 + dreaming 按白名单豁免，不依赖 LLM 判定。
- **D-3 衰减产品配置形态**：推荐 config 常量起步（PRODUCT_DECAY_PROFILES），
  DB 热更留到有运营需求时。
- **D-4 dreaming 合并实现**：推荐规则聚类起步（unkeyed 高相似碎片经 LLM 生成
  聚合表述，聚合不引入新信息=不违反"不臆造"）；矛盾识别用 LLM 低频批处理；
  合并动作走 supersede 版本链（碎片 is_active=False + superseded_by=聚合行）
  天然可审计可回滚。

## 红线（全程有效）

- 整合只调整置信度与有效性，**不臆造新事实**；动作全部留痕可回滚
- 原文/PII/租户不进 trace/metric；衰减**不删除不隐藏**（D4 边界）
- 不删改记忆正文；迁移 forward-only 依赖备份恢复
- 安全/权限/公共契约变化带双产品/双租户对照测试并登记 rag_smoke
