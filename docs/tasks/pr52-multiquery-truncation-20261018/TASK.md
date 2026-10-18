# PR #52：MultiQuery 截断、相关性与 RAG 审查修复

## 范围和停止条件

- 继续 `codex/rag-multi-query-p0-20261013` 原 worktree，起点 `d6007bf0328ad469eb45b2880d747511beab0747`；不修改共享 main，不重做全仓审计。
- 改写输出下限 1024，模型/温度/查询数/max 聚合/阈值/Golden/整轮预算不变。明确截断和部分评分，补安全的逐查询诊断；生产与评测共用改写和总 deadline。
- 用户追加并已确认：修复两份 RAG 报告仍存在的缺陷、补前置耗时埋点；较大建议只核实现状和记录后续方案。
- 免费门禁全部通过后，仅一次受信任 `workflow_dispatch multiquery=true`。总调用 ≤200、tokens ≤200000、费用 ≤CNY 0.50；账本无未核实或未定价项。
- 质量硬门禁：Top-1 ≥0.80、RC-024 三查询且多查询评分、Recall/Multi-hop 无可归因回退；逐例解释 Precision/Noise。失败即保留 Draft，记录根因及下一条最小建议，停止付费实验。
- Smoke 通过后才走现有 Prompt 发布/Active/Hash/Lock/回滚和必要 CI；全部硬门禁通过才解除 Draft、合并。

## 追加审查范围

- SQL metadata 字段名校验、`$contains` 字面匹配；contextualize 使用受治理模板。
- chunk cache JSON；BM25 缺少校验即拒绝，准确说明 SHA256 不提供来源认证及缓存目录信任前提。
- 引号、缺失类型导入、重复定义的最小修复；耗时分解不改变业务响应与授权。
- 不新增 Golden、不调阈值、不换模型/embedding、不启用未验收的缓存或候选答案流式。

## 验证

T0：语法、Diff、定向静态检查。T1/T2：Fake Provider、预算/隔离/装配、全部 backend/tests/prompts、16-fixture、隔离 PG SQL、远端 HTTP/服务接收埋点；免费 CI。T3：唯一一次真实 Smoke 和账本；发布只在通过后执行。
