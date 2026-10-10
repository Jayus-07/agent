# 迁移记录：trace 来源分类写入侧修复 + 回归测试

基线：main @ 098632a（与 origin/main 完全对齐）

## 背景

原工作区（主目录 codex/memory-system-profile-upgrade）的修复大部分已被整合进 main，
仅少数项遗漏。本分支只迁移**仍然缺失**的部分，不重复 main 已有的内容。

## 逐项终判（比对方法：git show <ref>:file 与工作区 git hash-object 对比，非看提交标题）

| 项 | main 状态 | 本分支处理 |
| --- | --- | --- |
| A. sql.guard 稳定 span id | 已合入（policy.py IDENTICAL） | 不重复；仅补测试 |
| B. router 改名「路由阶段」 | main 无 _routing_stage 框架 | **失效**，不迁移 |
| C. routing 阶段嵌套 | 同上，框架不存在 | **失效**，不迁移 |
| D. StepTimeline 折叠 | 已合入且更完整（比旧版多 244 行） | 不重复 |
| E-后端. embedding span | main 无 memory.embedding_query，问题不存在 | **失效** |
| E-前端. embedding 类型 | 已合入（trace.ts 实质相同） | 不重复 |
| F. travel.py runtime_domain | 已合入（3 处） | 不重复 |
| F-node. travel_graph_node | **缺失（0 处）** | ✅ 本次迁移 8 行 |
| G. test_sql_skill_followup | 已合入，且断言比旧版更强 | 不重复 |
| H. 回归测试 | **全部缺失（0 处）** | ✅ 本次迁移 |

## 本次改动

### 生产代码
- `backend/orchestration/graph/travel_graph_node.py`（+8 行）
  - `_stamp_execution_tags`（完成路径）与 `_maybe_cancel_active_run`（取消路径）
    补 `trace.tags["runtime_domain"] = "travel"`。
  - 根因：`classify_trace_source` 只认 `runtime_domain`/`domain`；旅游链路绕过主图
    Router，没有 `record_router_decision` 写该标签，于是落到
    `workflow_name=="agent"` 兜底分支被判成 AI 助手（线上实测 28/28 旅游 trace 全错）。

### 测试
- `backend/tests/travel/test_travel_trace_source_attribution.py`（新增，写入侧）
- `backend/tests/observability/test_trace_source.py`（+分类器侧 2 用例）
- `backend/tests/sql/test_sql_production_closure.py`（+稳定 span id 2 用例）
- `backend/tests/test_trace_fixes.py`（+summary 计数口径 3 用例）

## 验证

- 4 个文件合计：**69 passed, 0 failed**
- **变异测试（证明断言有效，非空跑）**：分别移除 runtime_domain 2 行、改回随机
  span id、把 llm_calls 计数改回含 embedding —— 对应测试均失败，随后还原。

## 未完成 / 风险

- B/C 判定失效：依赖的 `_routing_stage` 埋点框架始终未进 main。若后续该框架落地，
  需基于新基线重做（补丁思路已在旧 worktree 记录）。
- 取消路径测试用源码契约断言（该分支需 conversation context 存在活跃 run 才能走到，
  直接构造成本高且脆弱）；已与 `_stamp_execution_tags` 的行为测试互补。
