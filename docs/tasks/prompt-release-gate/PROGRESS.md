# Prompt 发布门禁当前状态

## 已完成

- 发布、评测、数据集治理、Prompt pin/hot-reload 与 Trace provenance 的后端和管理端实现已完成。
- 最近一次定向验证记录：后端聚焦套件 226 passed，新增管理端用例 4 passed，`npx tsc --noEmit` 与 `compileall` 通过。
- 管理端全量套件曾有 5 个既有货币格式断言失败（`$` / `¥`）；不能把该套件描述为全绿。

## 未闭合门禁

- 管理端 BFF `GET /prompts?keys=` 返回 HTTP 503，约 3 ms 快速失败；API Key 鉴权通过，仓库与 handler 直接调用成功，问题位于 HTTP 包装/依赖链。详见[故障记录](evidence/prompts-bff-503.md)。
- BFF 修复后仍需使用获授权的本地管理账号完成候选版本、评测、发布、Runtime epoch 和 Trace 元数据的浏览器验收。

## 下一步

1. 检查 BFF 中间件、依赖链与路由包装并修复 503。
2. 验证管理端请求成功，并完成验收标准中的发布与 Trace 流程。
3. 记录实际页面结果和任何预先存在的失败；不要保存账号密码。
