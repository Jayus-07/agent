# `/prompts` BFF 503

## 观察结果

- 管理端请求 BFF `/prompts?keys=` 返回 HTTP 503，约 3 ms 快速失败；API key 鉴权通过。
- 容器内直接调用 `repo.list_all` 成功，返回 1 条；直接调用 handler 也成功。
- 故障出现在 HTTP 包裹层。聊天链路使用 `render_sync`，不受此故障影响。

## 未完成项

根因尚未确认。先检查 HTTP 中间件与依赖链，再对比路由注册；修复后完成浏览器发布验收。
