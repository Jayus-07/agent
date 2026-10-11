# 选品漏斗与电商业务域开发规范

> **当前范围**：仓库已有 `backend/selection/` 与 `backend/selection_funnel/`、Tool/Provider、客服域。未来“电商运营主控 + 子域 Agent”是产品目标，不视为所有领域代码已完成。当前代码与注册表优先；不得因为概念相似把两个现有目录直接合并。

## 领域功能与架构界限

| 业务模块 | 目标职责 | 现有/候选事实源 |
| --- | --- | --- |
| 市场情报/选品 | 选品 brief、候选池、筛选、可行性验证、经济性、排行/报告 | `backend/selection_funnel/`、`backend/selection/` |
| 商品/内容 | 类目、SKU、Listing、标题与图片内容审核 | 独立领域 Service（先审计现有注册再新增） |
| 供应链 | 库存、补货、供应商与预测 | 基于真实库存 DB/Provider，不让模型伪造 SKU 数 |
| 营销 | 投放/ROI 与预算分析 | 真实广告数据、成本账本、SQL 安全范围 |
| 客服与售后 | 订单、客服、退款提案与人工接管 | 复用 `backend/customer_service/` 现有域和确认状态 |
| 财务与报告 | 毛利、费用、风险、审计报告 | 受控 SQL/预算/业务数据事实源 |

**Agent 的职责是理解任务、协调专家与解释证据；写操作审批、真实库存、订单状态和成本由 Domain/Repository/Tool Governance 控制。** 多 Agent 不等于每个功能新建一个永久独立 Graph。根据复用能力确定 Skill、Workflow、Domain Graph 的归宿；MCP 是协议适配器，不是所有数据调用必经的一层。

## 选品链和 Provider 数据

当前漏斗遵守 brief→pool→screen→verify→econ→rank→report 的概念阶段，但实际节点名称、来源、预算/配额、评分算法和报告结构以 `selection_funnel` 注册与代码为准，不在文档冻结固定推荐数量。报告中的市场需求、价格、销量、毛利和竞争情况要注明数据源、抓取时间、估算范围、币种和假设。外部 Provider 限流/反爬/过期不是“零竞争”证据，不允许默认填 0 并输出确定性排名。

**目标契约**：候选数据应有稳定幂等标识、tenant、商品/SKU 与来源，跨租户不相互去重或串读。**当前缺口 P1-8（覆盖整个选品漏斗）**：`backend/selection_funnel/import_pool.py` 的候选构建与去重之外，`backend/selection_funnel/import_pool_pg.py` 持久化的 `import_candidates`、`backend/selection_funnel/market_data_pg.py` 持久化的 `keyword_stats` 与 `product_reviews` 均未实现可验证的 tenant 字段/过滤；对应 `backend/sql/migrations/021_selection_funnel_pg.sql` 及导入、查看、按 batch 删除的 API/Service 调用链也必须检查可信 tenant 是否贯穿。整改需覆盖**商品候选、关键词统计、商品差评**的 Schema、存量回填、唯一/索引约束、读写/删除过滤、批次归属及资源授权；单独修商品池不得宣告整个漏斗多租户已隔离。上架、改价、广告、库存调拨、退款等未来执行动作**需要**提案、确认/审批、幂等与 UNKNOWN 处理，不能写成当前全部已实现。

## EC-01～EC-08 验收

| 编号 | 验收 |
| --- | --- |
| EC-01 | 选品入口、漏斗节点与报告字段对应注册表，子步骤失败不假成功 |
| EC-02 | 来源/时间戳/币种/估算和评分规则清楚，不凭空生成销量、利润 |
| EC-03 | Provider 超时/429/空结果按用户硬约束 BLOCKED 或有解释地 DEGRADED |
| EC-04 | 相同 SKU/竞品去重、缓存版本、跨渠道映射正确 |
| EC-05 | **多租户上线阻断/待验证**：商品候选 `import_candidates`、关键词 `keyword_stats`、差评 `product_reviews` 的导入/查询/按 batch 删除，及库存/订单/广告/成本的 tenant+owner 授权均需验证；当前 P1-8 涉及三类选品漏斗数据 |
| EC-06 | **写动作上线阻断/待实现**：未来上架、改价、广告、退款必须有确认/审批/幂等/未知结果恢复，现有代码不可推定已统一完成 |
| EC-07 | 域 Graph/Skill/Tool/MCP 注册无平行权限或运行时副本 |
| EC-08 | 报告和管理端按来源、推荐理由、Tool Trace 与评测版本可溯源 |

新增子域前在 [架构边界](../architecture/module-boundaries.md) 和 [评测治理](../evaluation/evaluation-governance.md) 里明确所有者、失败路径及最小 Golden 案例。不要把规划中的功能写成已实现。
