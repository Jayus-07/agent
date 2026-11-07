# TASK: 平台离线验收收口（offline-acceptance）

> 立项于 2026-11-07（接手自前序会话交接）。总门槛：**离线与免费 CI 先全部通过，
> 付费测试排最后且需用户单独授权**（隔离凭据、预算、Run ID）。全程不触发付费
> 模型/Provider 调用。

## 目标

在不动付费链路的前提下，逐项核实并补齐平台离线验收：

1. 收尾 travel-provider 数据集内容锁定（PR #357）。
2. 逐模块离线清单：RAG、SQL、Travel、E2E、客服、Planner/记忆、Tool/MCP、
   多 Agent/办公；逐项确认数据有效性、错误与跳过状态、Release Gate 三态、
   免费 CI 不会把无效结果算成通过。
3. 客服离线语义评测补齐：`forbidden_facts` 语义裁判校准前相关样本不得报完整
   质量通过；客服 fixture 实际索引与检索验收。
4. 可靠性离线验证：超时、重试、降级、幂等、断点恢复、SSE 断开、权限隔离、
   并发调度、人工确认、用量与成本归因、BadCase 回流。
5. 独立 Demo 与部署验收：学习清单 Demo（Python、结构化输出/工具调用、
   Redis/MySQL/API/并发）运行证据；Docker 重建与健康身份（动手前先确认
   环境与栈归属，不直接重建共享栈）。

## 范围与红线

- 所有改动走独立 worktree + 分支 + PR；主仓不直接改代码。
- gaps 工作树（`C:/Users/wh/.codex/worktrees/enterprise-agent-gaps/agent`，114 个
  未提交改动，含 `.github/workflows` 与 `Dockerfile`）禁止整批提交或覆盖；到相关
  模块时从最新 main 逐项核实、只迁移仍需要的独立改动。
- 共享栈只经 `devctl.bat rebuild` 换代码；不碰 `ragu1-*`、`agent-gh-runner`、
  observability profile；并行验证用错位端口与独立容器名。
- 未实际执行的验证不得报告为通过；无法验证标 `BLOCKED`/`UNVERIFIED`。
- 与 `docs/tasks/eval-closure`（已 COMPLETE）无重叠，勿重复实施。

## 验收

- [x] PR #357（travel-provider content_hash 锁定）合并，免费 CI 全绿
- [ ] 逐模块离线清单落档，每模块有证据结论
- [ ] 客服语义评测缺口补齐或明确标记
- [ ] 可靠性离线验证完成
- [ ] Demo 与部署验收完成
- [ ] 以上全绿后才允许进入付费测试（需用户另行授权）
