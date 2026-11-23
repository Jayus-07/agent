# 评测中心批次 6 · 进度

## 2026-11-23 · 6c 对比与台账去重(实施完成,待确认提交)

**worktree**: `D:\Program Files\workplace\agent-eval-batch6c` / 分支 `feat/eval-center-run-compare`(基线 main=c137b7c)

### 改动清单(4 文件)

| 文件 | 改动 |
|---|---|
| `frontend-admin/src/components/workbench/evaluation/proto/RunCompareDrawer.tsx` | 新增:发布评测对比抽屉(buildCaseDiff 纯函数 + 汇总并排 + tier 并排表 + 逐用例 diff 表) |
| `frontend-admin/src/components/workbench/evaluation/proto/RunsTab.tsx` | release 行加"对比"入口(同 key ≥2 条可选才显示);台账默认过滤 trigger=prompt_publish + "显示全部台账"开关 |
| `frontend-admin/src/components/workbench/evaluation/proto/RunCompareDrawer.test.tsx` | 新增 5 测试:diff 归类/排序/error-skip 口径/空侧、抽屉渲染(汇总+同态折叠+开关)、默认基准选择 |
| `frontend-admin/src/components/workbench/evaluation/proto/RunsTab.test.tsx` | 新增 2 测试:台账去重默认隐藏+开关、对比入口出现条件+抽屉打开 |

### 设计决策

- 入口选**行内"对比"按钮 + 抽屉**(改动最小),不恢复隐藏的一级"结果对比"tab——与批次 1.1"确认单/逐题都改抽屉"的 UX 决策一致,三标签结构不动。
- 归类口径:基准 pass→对比非 pass = 退步(红);反向 = 改善(绿);error/skip 与 fail 同按未通过;同态默认折叠(开关"显示两侧通过")。
- 默认基准 = 除预选外最新 published/approved/passed 记录,否则最新可选记录。
- 数据面纯前端:getRun ×2,零后端改动(方案 §6 拍板)。
- 旧 CompareTab(V2 专用 compare 端点)不恢复不删除——生产 V2 运行恒空,其保留与 V2 冻结拍板一致。

### 验证证据

- vitest 评测目录全量:11 文件 48 测试全绿(含新增 7 个)
- tsc --noEmit:0 错误
- npm run build:✅ 成功(exit 0)

### 坑

- vitest 环境 TS 常量 `as never` 不能 spread(TS2698),对象常量去 cast、mock 处再 cast。
- 断言锚点要用上屏文本(actor/时间),run_id 不渲染进 DOM 文本。

## 待办

- [ ] build 完成确认 → 展示 diff → 用户确认后 commit + push + PR
- [ ] PR 合并部署后生产实机验收:sql.generator v3/v4 真实 run 走一次对比(验收标准 7)
- [ ] 6d(veto_rules 一票否决)开工
