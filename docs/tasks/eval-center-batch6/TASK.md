# 评测中心批次 6:产品化增强(6c → 6d → 6a → 6b)

## 目标

承接方案 V2 §13.4 拍板(2026-11-23),在三标签结构内完成评测中心产品化增强;
完成后衔接管理端大迭代「企业 AI 平台运营与治理中心」Batch 0 审计。

## 批次与顺序(拍板,不可重排)

| 批次 | 内容 | 依赖 | worktree/分支 |
|---|---|---|---|
| 6c | 同 prompt 历史 run 对比 + CLI 台账去重(纯前端,复用 run detail ×2) | 无 | agent-eval-batch6c / feat/eval-center-run-compare |
| 6d | veto_rules 一票否决(先 suite JSON 字段)→ 规则配置化 → 评测任务预算上限 | 6c 合并后 | 届时新建 |
| 6a | 数据集产品化(Excel/CSV 导入、AI 生成进审核流、Golden 标注、版本冻结、双轨收敛) | 6d 合并后 | 届时新建 |
| 6b | 失败 Trace 闭环(证据深链、全域回流、整改复测、自动归类) | 6a 合并后 | 届时新建 |
| 6e | 指标扩展——与批次 3(cs/编排/旅游执行器)合并,不单独开工 | 挂账 | — |

## 6c 验收标准(本 worktree)

1. 发布评测列表可按 prompt_key 快捷筛选;选定基准 run + 对比 run(默认最新候选 vs 已发布版)
2. 对比视图:汇总行(通过率、tier 分层并排)+ 逐用例 diff 表(case_id 对齐;pass→fail 标红、fail→pass 标绿、同态折叠,可展开两侧说明)
3. 数据面纯前端:复用 GET /api/evaluation/runs/{id} ×2,不加后端端点
4. 入口以改动小者为准(恢复隐藏「结果对比」tab 或行内"与上一版对比"按钮)
5. CLI 台账去重:执行记录默认过滤 prompt_publish 触发源,带"显示全部"开关
6. vitest 定向测试覆盖 diff 归类逻辑与筛选行为;构建通过
7. 生产实机验收(合并部署后):对 sql.generator v3/v4 或 rag.qa 真实 run 走一次对比

## 硬约束

- 三标签不拆;蓝白视觉/侧边导航/六大分组不动
- 不加后端端点(6c);不做数据库迁移;不引外部评测平台
- 提交走双重 pathspec;push 走代理 7897;PR squash 合并
- 主仓 agent-cs-fix-b1(11 个未提交改动)与 agent-cs-order-consult(未证明合并)不得触碰

## 状态

- 2026-11-23:worktree 已建(c137b7c 基线),6c 实施中
