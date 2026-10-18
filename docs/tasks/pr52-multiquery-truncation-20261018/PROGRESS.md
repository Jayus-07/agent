# 施工进度

状态：**BLOCKED（真实质量与账本门禁失败，按用户停止条件结束本轮）**；MERGE_READY=false。实现HEAD=`a87df8316f820fa5cd008ec24fcab2d568022a88`。

## 最终结果

- 免费本地组合509 passed/51.81s，BM25补充36 passed/23.14s；GitHub最终实现免费门禁545 passed/35.92s，Contract Check/Secret Scan PASS。
- 唯一真实run [39108656690](https://github.com/Jayus-07/agent/actions/runs/39108656690)：fixture16/16与索引完整性PASS，严格质量FAIL、费用对账FAIL。
- Top-1=0.6667（4/6；RC-031重排不可靠缺指标，基线5/7），Recall@5=0.9286、MRR=0.8333、Multi-hop=0.8333、Precision@5=0.3541、Noise@10=0.5953。
- RC-024/031均6000ms rewrite timeout，回退原问题；RC-024 single_query，库存仍第一；RC-031仅剩约1.9秒重排窗口并ReadTimeout，原候选无分数透传。本轮三查询及max评分未真实验收。
- 113 calls/104861已记录tokens，108 settled=CNY0.117488；另5 needs_review（3 chat+2 rerank unpriced），最终费用UNVERIFIED，不能宣称费用硬门禁通过。
- [逐例指标、查询、排序、引用、Trace与账本原始汇总](SMOKE.md)。当前Artifact未提供异常逐调用账本，第三条chat异常的精确case归属仍未核实；timeout路径Prompt Hash/版本字段未完整留存，列为下一轮最小诊断修复项。
- 未再次付费、未改动功能代码以追分、未发布/回滚Prompt、未解除Draft或合并。Active v1与Lock一致，候选新Hash只代表文件，不代表已发布版本。

## 唯一真实 Smoke 已启动

- 实现 HEAD：`a87df8316f820fa5cd008ec24fcab2d568022a88`；GitHub [免费 CI 39108008370](https://github.com/Jayus-07/agent/actions/runs/39108008370) **545 passed / 35.92s**，Contract Check/Secret Scan 全PASS。
- [受信任 workflow_dispatch 39108656690](https://github.com/Jayus-07/agent/actions/runs/39108656690)：`multiquery=true`，HEAD同上；本轮唯一已创建的真实run。CLI第一次在dispatch前GET workflow处TLS超时，查询确认仍仅旧run后才用一次REST POST创建此run，未重复付费。
- 当前Active只读核验（docker内本机治理库）：v1/published/`d3ea6cc684a20879`，旧v2/draft/`898e7970527161cf`；新YAML候选Hash=`a9e9f66220217388`。读取57项Active状态与仓库Lock比较，0差异；未发布或回滚。
- PostgreSQL MCP凭据认证失败；上述治理状态通过既有本机PG容器的只读SQL核实，不操作共享业务数据。
- 该run安装阶段较慢：runner已安装`zh_core_web_sm 3.8.0`，但requirements的带SHA256 GitHub直接URL仍触发wheel下载（观测45MB且持续写入）；未中断、未重投。下载完成后正常进入fixture阶段。后续可评估把可信wheel按固定hash预缓存；本轮不另改安装或触发第二个run。

BM25重建/对账补充集合 **36 passed / 23.14s**；已并入免费CI。静态未定义名/重复定义定向检查PASS，最终Diff检查PASS。

## 基线与环境

- GitHub HEAD 已核对为 `d6007bf0328ad469eb45b2880d747511beab0747`，Open/Draft；原干净 worktree 从 `ae121b4` 快进同步。
- worktree：`C:/Users/wh/.codex/worktrees/pr52-main-sync-20261017/agent`；branch：`codex/rag-multi-query-p0-20261013`。共享 main 的既有修改未动。
- 原始 artifact：[39075781263](https://github.com/Jayus-07/agent/actions/runs/39075781263)，已下载到 `D:/tmp/pr52-round3-20261018/previous`。RC-024 rewrite=4973ms/512 completion tokens/1 query；RC-031=3656ms/499 tokens/3 queries。旧 artifact 未记录 finish_reason，不能从它推定具体枚举。
- RC-031 旧变体机械追加认证材料；`baseline_supplier_7` 以 0.3457 进入第5，原 Trace 只有聚合分数，不能确认获胜查询。已补每查询 Top-10 ID/文件名/分数诊断，禁止复制正文/内部元数据。
- 首次本机测试因缺省 PG 连接挂起，已终止。新建隔离容器 `pr52-round3-pg`，host port 55452，未连接共享业务库。首次迁移因缺少 004 所需兼容库 `agent_business` 失败；补建该隔离库后 init_db rc=0。

## 实现及免费证据

- 改写下限1024、生产子预算6000ms（真实基线3.656/4.973秒），仍取角色限制和共享8000ms剩余量的最小值。评测专用覆盖已删除；评测直接 retriever 入口现在绑定生产 deadline，不能验证超出生产窗口的链路。
- 增加终止原因、有效上限、用量、耗时、查询数、模板Hash/版本/来源。达到上限回退原query；不足目标查询数记 rewrite_truncated，单query明确degraded。
- Prompt 改为按问题选择证据面，保留多条件；不预设部门或认证。
- 缺分/非法分/部分失败明确partial；MQ小候选也真实评分，不填1；单query回退HTTP也继承deadline。
- 定向集合324 passed/51.56s（Prompt、16-fixture、MQ/身份/预算/交接）；免费CI原完整命令392 passed/47.57s。新增边界分别29 passed/24.97s、23 passed/5.78s。追加审查首轮107 passed/27.28s。最终组合验证仍在执行，以上不替代最终HEAD CI。
- Prompt alias 旧单测mock的是过时bump函数，已改为现行stage/dispatch且新增await断言，未改变发布生产实现。
- Python语法与diff-check已执行通过；缺少pytest-timeout插件的参数调用未执行测试，已记录并使用正常定向命令，未安装重复依赖。
- 本机异步PG退出偶有既有 `Event loop is closed` 清理告警，不将其描述为业务验收。

## 追加两份报告的核对与处理

- SQL字段名限制ASCII字母数字下划线；contains转义`!/%/_`，真实隔离PG查询验证字面子串语义。
- contextualize现在读取system/human模板且保留history；无效模板回退默认。
- chunk cache使用v3 JSON，旧pickle不加载；BM25缺少SHA256不再放行。SHA256仍仅检测损坏，不提供来源认证，BM25目录必须受信任；没有宣称关闭目录任意写攻击。
- 中文直引号不再被重复键强制转成闭引号；保留弯引号。清除等价关键词重复定义及summary采样重复包装；补类型导入/移除无用field导入，不做模块大重构。
- 新增服务接收→handler、pipeline获取、服务内处理、客户端往返及服务外差值，按既有child Trace关联；服务外差值仍不能单独区分连接池/网络/上游排队，需真实Trace测量后再归因。
- contextual prefix已实现：`indexing/indexer.py::_embed_text_for/_build_doc_level_text`与既有测试验证文档/章节/模拟问题前缀；不重复实现。
- AnswerCache已有scope/version结构，但`_ANSWER_CACHE_HARD_DISABLED=True`仍硬关闭。后续需权限重校验与失效实证后再评估启用。
- 已有stream分支与thinking事件，正文仍受Evidence Gate控制；候选答案外发需独立安全/终态契约验收。模型分级/更换embedding、扩充Golden、阈值校准/消融不纳入本轮付费实验。
- 报告的“回归集29题”已过时：当前统一manifest为`5.0.0-unified`、269条；`ci_golden`实际99条（RC-080移出），本轮固定`pr_smoke`仍8条。不因已有较大数据集就宣称全部269条已验证；后续消融需另选suite、预算和质量门禁。

## Round 4：超时诊断与迟到账本（本轮不付费）

本轮继续在原 PR worktree `C:/Users/wh/.codex/worktrees/pr52-main-sync-20261017/agent`、分支 `codex/rag-multi-query-p0-20261013`，起点 `1a21649f2c7904a201fc5340852bbbcd5ad1b30b`。共享 `main` 和独立的 `codex/llm-thinking-unify` / `04d72f18ad8476aab51c1612060461f529d415eb` 未修改。PR #52 继续保持 Open/Draft。

- 对历史5条异常账目复核后，Artifact 只有3 Chat、2 Rerank的聚合行，没有 row/reservation/call/provider request ID，不能与 RC Trace 唯一连接。RC-024/031 rewrite timeout 与 RC-031 Rerank ReadTimeout 保留为孤立调用证据，不假称对应某条账目；5条继续 `needs_review`、整轮费用 `UNVERIFIED`。详见 [逐条字段与证据](VALIDATION.md)。
- 当前有效配置路径是代码默认6000ms、`multi_query` 角色默认10s、总 deadline 8000ms；历史 runner env 与 DB 角色行未导出，历史源不可证明。本轮没增加 timeout/token/deadline。
- 新增安全改写阶段耗时、有效 timeout 来源、Prompt 实例Hash早写、单次收尾和迟到独立span；LLM预算路径回报真实 `settled` / `needs_review` 状态；Rerank 在共享 deadline 后丢弃分数但仍完成用量结算；请求身份/Trace上下文隔离由Fake Provider并发验证。
- 隔离 `pr52-rag-review-pg` 实际跑完整免费 PR 集合：624 passed / 56.28s / 0 skipped。E0 8 cases PASS；py_compile、定向 Ruff、diff-check、Contract Lock check、Prompt Lock check、本地 Secret Scan PASS。pytest 汇总2个既有 `TestCase` collection warning，并观察到Windows Proactor收尾 warning。GitHub PR CI、Contract Check、Secret Scan待提交推送后记录。
- 本轮未触发任何真实 Provider 请求或 Smoke；没有发布、回滚、解除 Draft、合并或把独立的 thinking-off commit 合入 PR。本轮优化判断不能由 Fake Provider推导模型性能，需未来受控真实验证。

停止条件仍有阻断：历史5条费用用量无法逐调用恢复，历史6000ms timeout来源未经runner/DB快照证实，真实三查询质量和新代码下真实账单未验证；**MERGE_READY=false**。本轮明确禁止真实付费 Smoke，下一步只做提交/推送与免费 GitHub 门禁。

## 付费前预算计划（待免费CI通过）

- 原真实账本118 calls/115574 tokens/CNY0.124661，0 unverified，settled118，差额0，approved_price_table。
- 当前改写仍2题，每题最多+512 completion；RC-024成功扩面预计多2次rerank及最多若干embedding，按140 calls/150000 tokens/CNY0.20规划，低于200/200000/0.50总门禁。
- 评测阶段128 calls/96000 tokens/CNY0.40；fixture阶段160/120000/CNY0.15保持，最终总门禁另统一对账，阶段预算不相加放大整轮额度。规划不是实际账单，以唯一Smoke完整账本为准。
- 预计评测本身不超过40 calls/60000 tokens/CNY0.10（旧整轮36次LLM中仅两次rewrite，fixture元数据调用占主要tokens；新增最多1024 completion与两次全候选rerank），留足评测128/96000/0.40及整轮余量。免费验证阶段未调用付费模型。
- 停止后下一条最小建议：补改写子任务分段耗时、渲染即写入Hash/版本，以及取消后的迟到用量回收/逐调用导出；先免费重放6s/8s窗口定位耗时，再确定同模型最小调整。本轮不实施、不再付费。
