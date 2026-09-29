# 统一路线图：SSE 断流修复 + SkillHub 批次 4-6 + auth P3 余量 + RAG 收尾

> 建立时间：2026-09-15 08:20（批次 2 会话交接 + auth 会话整合后）
> **维护约定：每完成一项，立即更新「进度看板」状态列并回填提交号；开工前置入 ⚙️。**
> 交接来源：批次 2 会话交接清单（见 2026-09-15.md 08:15 节）+ docs/auth/03 就绪清单 + RAG 优化实施计划。

## 0. 进度看板（实时更新）

| # | 线 | 事项 | 状态 | 完成时间 | 提交/备注 |
|---|---|---|---|---|---|
| P0-1 | SSE | 接手 events.py file 事件 WIP（43 行），补接入点+测试 | ✅ 完成 | 2026-09-15 08:25 | c0287f2；顺手修 URL scheme 误匹配（负向断言），9 测试全绿 |
| P0-2 | SSE | SSE 心跳保活 + 进度事件透传（~97s 断流止血） | ✅ 代码完成 | 2026-09-15 08:37 | e582731；ping 15s 节流 + 前端 ping 排除，72+27 测试绿；异步 run_id 轮询列为批次 4 后架构项 |
| P0-3 | SSE | 验收：①集成契约 ✅ b159ac0；②8001（已载心跳）实跑 market_research 全程 ✅——35s 流中静默空窗触发 ping 1 条、报告完整到达；③浏览器级实测留用户（3100 前端，链路各环节已分别验证） | ✅ 完成 | 2026-09-15 09:20 | 运行时验收通过 |
| P1-1 | 批次4 | CPR 吸收 + 全平台证据分型 | ⬜ 未开始 | — | |
| P1-2 | 批次4 | RAG 1.2 黄金集三组对比 + 0.2 mrr/recall 基线（与 golden 基建并单） | ⬜ 未开始 | — | golden 106 passed 已绿（07:34），一次重索引两单一起结 |
| P2-1 | 批次5 | 数据源欠账：榜单采集 / 评论实证 / 自家数据 | ⬜ 未开始 | — | 证据产量的根治（摘要级降级只是兜底） |
| P2-2 | auth | #8 business-service InternalTokenFilter fail-fast + WhatsApp 验签（Java） | ⬜ 未开始 | — | 需 build_java.bat；任务清单 #8 |
| P2-3 | auth | #9 ARCH-13 dept claim 三端（auth-service→网关→app） | ⬜ 未开始 | — | 任务清单 #9；owner_depts 零改动生效 |
| P3-1 | RAG | 4.2 错误码体系（需前端联动，建议与 P0 前端改动同批） | ⬜ 未开始 | — | |
| P3-2 | RAG | 阶段 5 企业级用量审计（5.1-5.8） | ⬜ 未开始 | — | 见 RAG 优化实施计划 |
| P3-3 | RAG | C7 参数网格实验（阶段 2 完成后做） | ⬜ 未开始 | — | |
| P4-1 | 批次6 | CloakBrowser 六项风控清单 → engine 开关 | ⬜ 未开始 | — | 风险最高，压轴 |
| P4-2 | 批次6 | email.watch 主动通知闭环 | ⬜ 未开始 | — | 依赖批次 1 EmailSkill |
| P5-1 | auth | P4：Kafka 信封 subject_type/actor_source 双端（可选 SASL） | ⬜ 未开始 | — | 依赖 #9 |
| P5-2 | auth | P5：guest 路由策略 + department.ts 登录态化 + dept_code 对齐 | ⬜ 未开始 | — | 可与 P4 并行 |

## 1. 已完成基线（本路线图的起点，勿重做）

| 线 | 已交付 | 凭据 |
|---|---|---|
| SkillHub 批次 1 | EmailSkill 四能力，OAuth 已通，真实搜索 ok | d3f9f19 |
| SkillHub 批次 2 | market_research 五段式 DAG，端到端验收通过（156s，7 证据/15 章节） | e9dc554 + e6aefa7 |
| SkillHub 批次 3 | 评估拆分 + decision_log 十字段 | b6626a9（golden 106 passed 全绿 07:34） |
| RAG 阶段 0-3 + 4.1/4.3/4.5 | S0 修复/前缀体系/切分修补/双层缓存/审核态软过滤/表格行描述 | 0e3fb22 等，rag 全目录 445 passed |
| auth P3 Python 侧 | identity.py 单一入口 + IDENTITY_SOURCE=header 已接线生效 + CS 断链 + reviewer + 工具身份 + 8000 收口 | addf7a8 |
| P2 网关鉴权 | enforce 上线，双模式 19/19 | a89f97a 系 |

## 2. P0 专项：SSE ~97s 断流（当前唯一 P1 级产品缺陷）

**现象**：长 workflow（如 market_research 156s）服务端成功，但 SSE 流约 97s 后前端断流，UI 卡「生成回复中」，final_answer 丢失（截图存档）。

**根因方向**：代理/浏览器对无数据 SSE 连接的空闲超时（97s ≈ 常见代理 idle timeout）；workflow 执行期间事件稀疏。

**修法（止血优先）**：
1. 完成 events.py 的 file 事件提取（工具落盘文件透传前端——本身就是进度事件的一种）；
2. SSE 生成器加**心跳事件**（如每 15s 一条 `{"event":"ping"}`），前端 fetcher 忽略未知事件但保持连接计时重置；
3. workflow 长任务期间透传步骤进度（step 事件已有，确认间隔不超过空闲阈值）；
4. 前端 authFetch/chat 流：确认无「读超时主动断开」逻辑。

**验收**：3100 前端浏览器实测 market_research 全程 >150s 不断流，报告正常渲染；8001 验证后端跑完可收。

**根治（后置架构项）**：长任务异步化 run_id 轮询（POST 返回 run_id → GET /runs/{id}/status）。

## 3. 约定

- 并发会话共存：动手前 `git status` + 文件 mtime 确认无人编辑；本看板事项与批次 2 会话 WIP 的边界以本文档为准。
- 全量回归门禁（人工）：`./.venv/Scripts/python.exe -m pytest backend/tests/ -q -p no:randomly --no-cov --ignore=backend/tests/evaluation/test_eval_golden.py`
- 每笔提交回填看板提交号；一批完成后更新本文档「进度看板」。
