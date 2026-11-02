# Q1 残留盘点与处置依据（2026-11-02）

仅只读查询共享 PostgreSQL :5433，未删除共享数据。查询脚本为 d:/tmp/cs_q1_inventory.py，不入库，不输出问题原文。

| 清单项 | 当前事实 | 处置与依据 |
|---|---|---|
| unanswered_questions | 29 条，2026-10-04 至 10-31 UTC；含多个用户/租户，entry=16 仍在 | 保留。intake 不会消费删除原记录；日期与“当日测试批”预设不符，且有候选来源引用，不能证明全为可删除残留；当前 API 仅查询，无 DELETE |
| eval_bot tickets | 60 open；60 有 conversation_id、29 有 handoff_id；41 条可关联到 open 会话；无坐席指派 | 保留关联链。未达到“非活跃且无引用”的删除前提；不直删工单形成悬挂 handoff/会话，也不以批量关闭改变在途状态。后续按测试会话整体退役处理 |
| eval_bot confirmations | 38 expired、2 cancelled，无 pending | 保留终态记录用于幂等/历史追踪；当前没有待确认残留需要取消 |
| eval-cs-* checkpoints | distinct thread_id=0 | 无此模式残留；不扩展为删除其他线程 |
| rag case 6e6e3daa / smoke_refuse_mvmdmqfr | published v1.0.0 有 1 item；suite smoke_suite_mvmdmqfr 与 ui_retrieval_suite 引用；后者有 7 次 run | 必须保留发布引用与运行快照，不归档/删除 |
| archived case 的 398 approved candidates | 398 条，review_candidate 拒绝覆盖终态 | 用户已确认保留审核历史；候选发布过滤另立小 PR，当前 UI 批量发布限制仍存在，CS 继续使用合法 case_ids 的 API 发布路径 |
| govtest / p7v / p9g- | 扫描当前数据库所有含 tenant_id 的业务表，上述 tenant 前缀记录为 0 | 本次未发现对应租户残留；不据名称猜测删除其他对象。历史 govtest 数据曾属并行会话，禁止扩大到 MCP/其他数据库盲删 |

以上保留不表示数据已清零。Q1 采用 TASK 的“逐项清零或标注保留依据”验收方式；仍存的 open eval_bot 工单可能影响运营看板统计，受控试用需将测试会话隔离/退役后再使用业务统计作容量或质量结论。
