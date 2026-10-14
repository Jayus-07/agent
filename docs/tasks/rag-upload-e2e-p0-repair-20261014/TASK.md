# RAG 上传 E2E P0 修复

## 目标

基于 PR #55 合并基线 `cfa482e0bce5b1979f82304cde43bdf7d20dac5e`，只修复真实上传 E2E 中 Batch A 的内容完整性与候选发布安全缺陷。保留现有 RAG 架构、Chunk 策略、鉴权与生命周期机制。

## 本轮范围

1. 保留 FAQ 问题文本，并确保最终 Leaf 的 Parent 引用指向实际输出 Parent。
2. 覆盖审计只核对过滤后的最终输出 Leaf；零有效 Leaf、无效 Chunk ID 或悬空 Parent 引用必须由既有 QualityGate 阻断。
3. 不完整候选失败，不发布 Active；candidate mode 不改写上一代 Active registry。
4. 保留 PR #55 合并后原始 E2E 报告和本轮可追溯的复现/回归证据。

## 范围外及停止条件

- Batch B（PDF、Excel、Worker 内存/终态、migration 漂移）和 Batch C（18 类真实 API/索引/检索复验）本轮不施工。
- 不执行付费 Provider 评测，不修改 Clash/代理配置，不写入正式业务库。
- 完成 Batch A 定向回归、自查和修复证据后停止；`RAG_UPLOAD_E2E_VERIFIED` 保持 `false`。
- 不自动合并 main。独立修复 PR 供用户审阅。

## 验收

- FAQ 回归测试在基线复现问题，修复后问题文本保留且所有已声明 Parent 引用有效。
- 后过滤 Coverage 覆盖率和 Quality Record 一致；零 Leaf 或悬空 Parent 为硬异常。
- 覆盖审计/完整性失败不发布 Active；candidate failure 保留已有 Active。
- 定向 RAG 回归和 Python 语法检查实际执行并记录结果；未运行的门禁不得报告 PASS。
