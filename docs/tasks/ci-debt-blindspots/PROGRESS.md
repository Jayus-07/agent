# PROGRESS — ci-debt-blindspots

> 进度台账：证据、阻塞、下一步。坑位续接 ci-regression-debt 的 1–31，从 32 起记入本文件。

## 立项记录（2026-11-02，守护轮会话分析产出）

### 已完成事实（立项依据，勿重做）

| 事项 | 证据 |
| --- | --- |
| T1（#232）全量验证过关 | v29 run 41363455037（main@257af71）travel suite 全绿 |
| v29 唯一红已根因清偿（坑位 31：#237 分片收口漏 test_distributed_gate.py / test_tool_stats_aggregation.py，裸 db15 跨 worker flush） | HB-DEBUG（心跳 8 轮全成功 + all_keys=[]）+ xdiag 时间线（gw1 dgate vs gw0 admission）；存档 /d/tmp/ci-debt/v29/ |
| 守护轮修复 PR #251 合 main | merge 832a572；提交 a20367f（testing-guide 两小节）+ 9bc3cc2（两文件分片）+ ae292c6（PROGRESS 回填） |
| 收口轮 v30 全绿 | run 41365926765（9bc3cc2）14/14 job success |
| testing-guide.md 已含 PYTEST_XDIAG 三件套 + flushdb 分片纪律 | #251（a20367f） |
| **盲区发现：7 目录 19 文件 209 用例不在 full_regression matrix** | `ls backend/tests/*/` vs matrix paths 对差；`pytest <7 目录> --collect-only` = 209 tests（15.22s 无 import 错误）；目录：agents/eval/router/selection_decision/selection_funnel/tasks/tool_governance |
| flushdb 调用点五个文件全部分片 | grep 实证（admission×2 / runtime / dgate / tool_stats） |

### 当前状态

- TASK.md 已立项（总计划 P0–P3、验收清单就绪），等待下一会话执行。
- scheduled 首跑（cron 2026-11-02 03:00 UTC）截至立项时未到点/未出现，P0 待核对。

### 下一步（下一会话第一动作）

1. 按 TASK.md §二建工作树与分支（parent ≥ 832a572）。
2. P0 scheduled 核对。
3. P1 matrix +7 目录 → 本地 xdist 基线 → PR → dispatch suites=misc 首曝清偿 → 全量收官。

## 坑位（32 起，续接 31）

（暂无；首曝清偿过程中按「一轮一证据」纪律补充。）

## 阻塞与移交

- 无阻塞。移交产品域两项维持边界（见 TASK.md §六）。
