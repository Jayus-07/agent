# 下一会话开工提示词（复制即用）

> 用法：合并 PR #299 后，开新会话粘贴以下全文。

---

继续 agent 仓库的旅游任务 **travel-value-showcase**（旅游预算真实化 + 首页「推荐行程」能力橱窗）。

## 开工前必做（顺序不可跳）

1. 按仓库 AGENTS.md「Git 并行开发规范」建任务工作树（从**最新 origin/main** 建，先 `git fetch`；确认 PR #299 已合并，否则先报告）：
   `git worktree add "D:/Program Files/workplace/agent-wt-travel-value" -b codex/travel-value-showcase origin/main`
   并按惯例为 frontend 建好 node_modules Junction；本地测试需要 .env 时从主仓库拷贝。
2. 通读任务文件：`docs/tasks/travel-value-showcase/TASK.md`（差距矩阵/阶段计划/红线/决策点/验收清单都在里面，本文不重复）与 `PROGRESS.md`。
3. 按仓库纪律先读 `.zcode/skills/agent-repo-pitfalls`（并行会话/共享栈/pytest 冻结/容器归属）。

## 用户已拍板口径（2026-11-03/04 原话）

- 「目前的行程结果经济还是太贵了 压到最低预算可以是多少钱 很多人不太想花钱 除了交通方式的话」→ 新增更低价档位，**展示名「平价出行」（chip 短名「平价」），展示侧零出现"穷游"字样**（"穷游"只作为输入触发词收录词表）。
- 验收数字：平价档 2 人 2 天总价 **≤ ¥400**、3 天 **≤ ¥600**（估算口径，城际大交通不计入）。
- 「上一版的首页 有推荐行程 这个要加上 用真实的生成行程结果 加一个按照经济 风格 区分」→ 首页照片卡橱窗，数据源只有 `fetchTravelTripsV2` 真实行程，筛选用 `brief.tier` + `brief.preferences`，空组合如实空态、**禁止任何种子/演示数据**。
- 「推荐行程区块的功能你要深入一下理解一下是让用户最快了解项目的功能」→ 橱窗是能力展示：卡片突出真实预算构成/免费门票/评分/诚实披露，点击透传完整工作台。

## 执行顺序

TASK §4 阶段计划：D0 侦察定稿（低价酒店样本实测 → 定额数字拍板）→ B1 平价定额与地板 → B2 档位贯通 → B3 排程校验适配 → B4 首页橱窗 → B5 平价样例真实生成 3 条 → V1 总验收。每阶段独立 commit（双重路径限定）+ PR；改定额常量必须连带同步守卫断言/金标/缺口卡文案（TASK §3 红线 3）。

## 验收底线

TASK §6 清单逐项打勾：地板数字单测固化、橱窗空态诚实、UI 零"穷游"、economy/comfortable 数字零回归、vitest travel 面 + travel 域 pytest 全绿、rebuild healthy、真机逐屏截图回填 PROGRESS。

## 已知环境事实

- 共享栈 3100/8000 容器 healthy；重建前 `git fetch && git merge-base --is-ancestor origin/main HEAD` 确认 base 含全部已合并 main（PR #286 教训：并行重建致已合并功能静默回滚）。
- frontend 测试基线：vitest travel 面 275+、tsc 既有 17 错（含 cs2 1 条非 travel，零新增为底线）。
- 若我（用户）中途给新要求：先更新 TASK.md 再动手。

---

