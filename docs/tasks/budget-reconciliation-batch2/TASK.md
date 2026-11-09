# TASK: 预算治理第二批——待对账闭环 + 存量迁移 + 页面可用

> 上游：第一批 1.1–1.4（同分支 `fix/budget-reconciliation-batch1`，**未提交**，待用户确认）。
> 方案来源：tmp/zcode_batch1_prompt.md §三·补（第二批路线）。

## 目标

1. 待对账核销/归档动作：settled_actual / released / written_off / archived + reason + resolved_by + resolved_at；核销同步释放账本占额、写审计、不物理删除；关闭后默认队列不显示、可筛选查看。
2. 存量 121 笔批量迁移：分类 dry-run 报告先行 → 批量归档/释放/核销 → 写审计 → 核对占额归零。
3. 页面折叠/筛选/批量操作：明细默认折叠、按时间筛选、勾选批量核销、历史明细不再刷屏。
4. 测试/评测数据隔离：按租户/用户/请求标记过滤，生产视图默认不显示 test/eval 数据。

## 范围边界（不做）

- 第三批内容（quota_profile / 用户额度搜索恢复 / 月度报表）
- 物理删除任何账本/预占行
- 未经用户逐类确认的存量写库操作

## 关键设计（评审后拍板）

- **resolution 轴独立于 status 枚举**：`budget_reservations` 加 `resolution/resolved_by/resolved_at/actual_cny` 列（status 枚举不动，满足"needs_review 语义不写死"）；关闭态 = `resolution <> ''`。
- **审计用独立 action 表** `budget_reservation_resolutions`（append-only），`budget_events` 是阈值告警表不适用。
- **所有"未结算"消费点过滤 resolution=''**：`summary().unsettled_reserved`、`reconciliation_summary().held_cny`、`list_pending_review` —— 落实"1.4 不得依赖 needs_review 永不关闭"的前瞻约束。
- **账本释放语义照抄既有 settle/release SQL**（同 reservation id 的 4 行 scope×period 各自更新）；`settled_actual` 额外 `used_cny += actual_cny`（唯一记账成本的动作）；released/written_off/archived 仅释放不记成本（不伪造费用）。
- **幂等**：仅 `status='needs_review' AND resolution=''` 可被核销；重复调用返回 already_resolved，不得二次释放账本。
- **迁移编号 144**（共享库已被并行会话占用 143_memory_product_scope，未合入 main）。

## 验收标准

- 单笔/批量核销 API：幂等、审计留痕、账本占额同步释放、closed 默认不显示可筛选。
- summary/reconciliation 与队列口径一致（closed 不再计入占额）。
- dry-run 分类报告能解释全部 121 笔存量（数量+金额+建议动作）。
- 前端：明细折叠 + 时间筛选 + 单笔/批量核销 + 分类过滤可用。
- 定向回归绿；rebuild 健康。

## 状态

- 进行中（B2-P0 调研完成，P1 开始）。
- 风险：第一批未提交即叠第二批，最终 PR 含两批改动，需用户确认后分两个 commit 提交。
