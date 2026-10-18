# 施工进度

状态：IN_PROGRESS；MERGE_READY=false。最终组合免费命令 **509 passed / 51.81s**，含追加审查与完整 Prompt/16-fixture；待 GitHub 对最终提交执行免费门禁。

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

## 付费前预算计划（待免费CI通过）

- 原真实账本118 calls/115574 tokens/CNY0.124661，0 unverified，settled118，差额0，approved_price_table。
- 当前改写仍2题，每题最多+512 completion；RC-024成功扩面预计多2次rerank及最多若干embedding，按140 calls/150000 tokens/CNY0.20规划，低于200/200000/0.50总门禁。
- 评测阶段128 calls/96000 tokens/CNY0.40；fixture阶段160/120000/CNY0.15保持，最终总门禁另统一对账，阶段预算不相加放大整轮额度。规划不是实际账单，以唯一Smoke完整账本为准。
- 预计评测本身不超过40 calls/60000 tokens/CNY0.10（旧整轮36次LLM中仅两次rewrite，fixture元数据调用占主要tokens；新增最多1024 completion与两次全候选rerank），留足评测128/96000/0.40及整轮余量。免费验证阶段未调用付费模型。
- 下一步：最终组合免费测试、PR免费CI；通过后只dispatch一次。尚未执行本轮真实Provider、发布、解除Draft或合并。
