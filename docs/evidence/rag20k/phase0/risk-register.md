# RAG 20k 阶段 0 风险登记册

> 来源：审计报告 §7.2 主要风险与控制（2026-09-19 建册）。`trigger/mitigation/rollback` 三列逐字取自报告的"监测信号/预防措施/触发后的动作"。
>
> **owner 指定（2026-09-19）**：11 项 owner 均为项目所有者 **Jayus-07**（单人项目，兼任业务/运维/安全角色）。**due_date** 以阶段/里程碑锚点表示——审计报告未设日历时间表，各阶段出口门即截止条件（如"阶段 1 出口前"= 阶段 1 出口门通过之前必须处置）；对应日期在阶段排期冻结时回填为日历日。报告 0.6 的完成标准（owner、截止日期、触发条件、缓解、回滚）至此全部具备；出口门（机器部分）只校验本文件存在，签字部分由评审人工把关。

P0/P1 划分规则：影响"致命"，或概率与影响同时为"高" → P0；其余 → P1。

| ID | 风险 | 级别 | 概率/影响 | owner | due_date | trigger | mitigation | rollback | evidence_ref | status |
|---|---|---|---|---|---|---|---|---|---|---|
| R1 | 历史 RAG 数据没有 tenant 归属 | P0 | 高/致命 | Jayus-07 | 阶段 1 出口前 | tenant 回填空值或歧义数 > 0 | 业务映射表、隔离租户、生产 fail-closed | 隔离记录，不进入检索；人工确认后再迁移 | [Q1-Q10.json](decisions/Q1-Q10.json)（Q1） | open |
| R2 | PG 原生排名低于 BM25 | P1 | 中/高 | Jayus-07 | 阶段 2 出口前 | 500 黄金集任一质量指标未达标 | 统一分词版本、影子运行、两轮单变量调优 | 保持 BM25 回滚路径并评审 OpenSearch | [golden/validation-summary.json](golden/validation-summary.json) | open |
| R3 | 迁移回填锁表或膨胀 | P1 | 中/高 | Jayus-07 | 阶段 1 出口前 | 锁等待 P99 ≥ 1 s、磁盘 ≥ 70% | 小批提交、限速、expand/contract、预估空间 | 暂停回填，释放锁，恢复旧读取路径 | （阶段 1 产出后回填） | open |
| R4 | 索引并发造成丢更新 | P0 | 高/高 | Jayus-07 | 阶段 1 出口前 | 对账不为 0、索引版本倒退 | 阶段 1 单写者、唯一键、事务、版本比较 | 停止 writer，按注册表重放并重新对账 | （阶段 1 产出后回填） | open |
| R5 | 查询服务仍在启动时写索引 | P1 | 中/高 | Jayus-07 | 阶段 1 出口前 | RAG Query 数据库写审计 > 0 | 只读 DB 角色、启动路径拆分、测试 | readiness 失败并阻止流量 | [baseline 清单](baseline/8ad9e77f1b4fd9f4/baseline-manifest.json) | open |
| R6 | Embedding 配额不足或成本失控 | P0 | 高/高 | Jayus-07 | 20k 导入启动前（Q6 解决前禁止导入） | 429 ≥ 0.1%、预算告警 | QPS/TPM 双限流、批量、缓存、预算上限 | 降并发/暂停导入，不做无限重试 | [Q1-Q10.json](decisions/Q1-Q10.json)（Q6） | open |
| R7 | 多副本本地状态导致行为分裂 | P0 | 高/高 | Jayus-07 | 阶段 4 出口前 | 跨副本取消/进度/调度测试失败 | Redis/PG 权威状态、独立 scheduler | 阻止扩容或回到单副本止血，修复后重测 | （阶段 4 产出后回填） | open |
| R8 | 单主机卷导致主机故障不可恢复 | P0 | 中/致命 | Jayus-07 | 对象存储方案落地前（禁止多主机上线） | 文档只有一个本地副本 | 对象存储、版本控制、恢复演练 | 不宣称 HA；恢复前停止写入 | [Q1-Q10.json](decisions/Q1-Q10.json)（Q9） | open |
| R9 | 新旧版本混跑时 schema 不兼容 | P1 | 中/高 | Jayus-07 | 阶段 5 canary 前 | canary 旧实例错误、未知字段 | 向后兼容 expand migration、双写、功能开关 | 回滚应用，保留新字段，不执行 contract | （阶段 5 产出后回填） | open |
| R10 | 指标口径漂移 | P1 | 中/高 | Jayus-07 | 阶段 0 出口前（0.3 双跑完成） | 同一输入不同 runner 指标差 > 0.005 | 固定 runner/数据/参数哈希、机器可读结果 | 验收作废，先修评估链路 | [evaluation/](evaluation/)（待 0.3 产出） | open |
| R11 | 当前工作树存在并行未提交改动 | P1 | 高/中 | Jayus-07 | 阶段 1 启动前 | 实施 diff 混入无关文件 | 实施前提交/暂存用户改动或创建隔离 worktree | 停止合并，重新基于明确基线拆分提交 | [baseline 清单](baseline/8ad9e77f1b4fd9f4/baseline-manifest.json)（已冻结 181 项变更指纹） | open |

## 变更窗口与证据目录规则

- 变更窗口：阶段 1 之后的任何 schema/索引结构变更必须先在登记册登记 trigger 与 rollback，再排入变更窗口；窗口时间由 owner 指定后冻结。
- 证据目录：`docs/evidence/rag20k/phase0/` 下每类证据一个子目录；`baseline/` 按不可变 capture_id 分目录，禁止覆盖；摘要类 JSON 只允许 `ok/failed/blocked` 三种 status。
- 外部材料（原始 20k 语料、授权合同、供应商配额函）不进入 Git，只在本登记册与 [README.md](README.md) 记录路径与状态。
