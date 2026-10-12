# TASK — RAG 检索性能专项优化（R0 + R1）

## 目标

在独立工作树/分支上完成 R0（性能基线与指标）与 R1（请求级 Embedding 复用 R1-A、
简单问句误触发 MultiQuery 修复 R1-B），并交付可与修改前对照的真实数据。
完成后立即停止，不实施 R2/R3，不合并 main。

## 范围

- R0：复用既有 Trace / Prometheus / record_rag_stage，补齐
  阶段耗时、每请求 embedding 调用/唯一输入/复用、TTFT 与总生成耗时、
  答案缓存命中/绕过、rag.search 父子耗时口径。
- R1-A：共用 embedding 调用层的请求级去重 + singleflight + 有界生命周期；
  缓存键 = 模型身份 + 实际输入；不得跨模型、跨文本变体复用。
- R1-B：统一主分类器与兜底的简单事实问句优先级，修复「退款审核时间是多少？」
  在降级路径误触发 LLM 改写。
- R2/R3：只交付设计、依赖与风险，不写生产逻辑。

## 非目标

- 不重构 RAG、不新增表/微服务/Agent 节点、不新建 LLM 计费或取消框架。
- 不改 Hybrid / RRF / Rerank / Evidence Gate / 引用溯源语义。
- 不修改向后兼容接口。

## Git 基线

- 主仓工作区：`D:\Program Files\workplace\agent`（原 main `631f153`，只读）
- 任务工作树：`.worktrees/rag-perf-r0r1-20261012`
- 分支：`task/rag-perf-r0r1-20261012`
- 基线提交：`d1b9668`（= GitHub 最新 main，PR #33 / #36 已合并）

## 验收

1. 五类指标可从真实请求读取，且 embedding 计数与真实 provider 调用一致。
2. 相同 query 的 Doc/Chunk 向量化只出站一次；不同 k 不重复 embedding；
   不同模型/文本变体不串用；并发同 key 只出站一次；失败不入缓存。
3. 简单事实问句不再触发 LLM 改写；复杂/多子句问句仍触发。
4. 定向测试全绿；真实链路有修改前后数据；检索与引用不退化。
5. 报告含 Git 基线、修改前后调用链、精确重复位置、改动文件、性能数据、
   安全/权限/计费验收、R2/R3 方案与回滚方法、Commit SHA 与 R0/R1 结论。
