# Testing

## Backend

- 使用 pytest
- 新增功能必须覆盖：
  - Happy Path
  - Error Path
  - Edge Case
  - Fallback（如适用）

## Frontend

- 使用 Vitest + @testing-library
- 新增功能必须覆盖：
  - Render
  - User Interaction
  - State Changes
  - API Error

## Test Review Checklist

Review 测试时必须检查：

- DRY：重复 Fixture / Helper 必须抽取
- Mock：MagicMock 使用 spec 或 autospec
- Assertion：避免依赖 message 文本，优先断言状态、数据和结构
- Async：禁止依赖 queue.empty()，优先使用 sentinel 或 await queue.get()
- Trace：验证 Trace、Span、Metadata（适用时）
- Reliability：补充 Retry、Timeout、Cancel、Resource Cleanup 等关键场景（适用时）

## Manual Verification

Claude 自动测试通过 ≠ 功能完成。

以下情况必须人工验证：

- 所有 UI 修改
- 页面交互
- SSE / Streaming
- 浏览器兼容行为
- 影响用户体验的功能