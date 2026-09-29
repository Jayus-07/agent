# 上下文预算管理 Phase 2 实施计划 —— 实机验证 MVP + L4 Context Collapse

> 需求规格逐字持久化（2026-09-22 13:08）。上一阶段 MVP 已提交：`29478df`。
> 执行原则：**先证明 L1/L2/L3/Preflight 在真实链路稳定，再引入 L4。**

---

## 〇、开始前先检查仓库状态

当前仓库是多会话并行开发。开始前必须：

```bash
git status
git log --oneline -10
git show --stat 29478df
```

确认 `29478df` 仍然存在且代码已在当前分支。

注意当前可能仍存在其他会话留下的未提交文件，例如：`rule_guard.py`、
`test_order_service.py`、`quality_records`、`tsconfig`，以及之后新增的其他脏文件。
**这些都不是本任务内容。**

禁止：`git reset` / `git checkout .` / `git clean` / 覆盖其他会话文件 /
把其他会话修改一起 commit。本次仍然必须使用路径限定提交。

---

## 一、第一阶段：让 MVP 真正生效

上一阶段说明：app :8000 仍运行旧镜像。先检查当前 docker compose 服务状态。

允许重建本任务需要的服务，但不要无脑 `docker compose down`。优先最小影响方式，
例如 `docker compose up -d --build app`。

如果 worker 同样 import / 执行了新的 `backend.context_budget`，或者 Tool / Skill
执行链实际运行在 worker 中，则检查后决定是否还需要 `docker compose up -d --build worker`。
**不要凭提示词猜**：必须先根据代码调用链和 compose 配置判断哪些 context_budget
代码由 app 执行、哪些由 worker 执行，只重建真正需要的容器。

## 二、重建后先做健康检查

至少检查：app health、APISIX 路由、`/chat/stream`、PostgreSQL、Redis、
worker（如果本次涉及）。确保没有：ImportError / ModuleNotFoundError /
Pydantic schema error / Prometheus duplicate metric / FastAPI startup error /
循环 import。查看 app 日志。

如果 context_budget 导致启动失败：**优先修复，不要绕过或关闭功能。**

## 三、验证实际预算

当前环境 `LLM_CONTEXT_LENGTH=8192`，**不要按文档 4096 示例判断，也不要改成 4096**。

```
input_budget = 8192 - CONTEXT_OUTPUT_RESERVE_TOKENS - CONTEXT_SAFETY_RESERVE_TOKENS
```

通过真实运行代码确认最终值。日志/测试报告明确输出：
`LLM_CONTEXT_LENGTH` / `CONTEXT_OUTPUT_RESERVE_TOKENS` /
`CONTEXT_SAFETY_RESERVE_TOKENS` / `actual input_budget`。

## 四、实机验证 L1

构造或找到一个能产生较大 Tool Result 的真实请求（明显超过 TOOL_INLINE_MAX_TOKENS）。

验证真实链路：Tool → ToolResultBudgetGuard → step_results。

检查清单：
1. 原始工具执行成功
2. 超预算后触发 L1
3. step_results 中不再保存完整大文本
4. ToolResultRef / preview 正确
5. original_tokens 正确
6. preview token 没有超过配置
7. 后续 Skill / Reporter 仍然可以正常运行
8. 没有因为结果类型从 str 变成结构化数据而导致 prompt 拼接错误
9. context SSE 正常产生
10. 前端收到「已优化上下文 · 节省 xxx tokens」

特别检查：旧 Markdown Tool 与新统一 Tool 是否都真的经过 L1。不要只依赖单元测试。

## 五、实机验证 L3

构造多步骤任务（Tool A → B → C → Reporter），让 previous_outputs 累计超过
`PREVIOUS_OUTPUTS_MAX_TOKENS`。验证：最新结果优先、旧结果压缩、总 token ≤ budget、
Reporter 正常完成、必要 status/tool/step_id 信息不丢。

**重点风险**：如果 L3 为满足预算把"真正后续需要的数据"裁掉导致答案错误——
不要简单提高 token budget。应检查 previous_outputs 的数据契约，区分：
- 用于 Prompt 的 projected output
- 工作流真实 step_results

必须保证：**L3 只压缩 Prompt Projection，不破坏工作流权威结果。**

如果当前实现直接修改了权威 step_results，请修正。理想结构：

```
raw step_results → previous_outputs projection → MicroCompact → LLM Prompt
```

而不是 raw step_results 被永久覆盖成 preview（L1 除外，L1 本身就是工具出口预算策略）。

## 六、验证 L2

构造 20+ 条历史的长会话并使历史达到预算。验证：
- 数据库 chat_messages 完整存在
- 前端历史完整显示
- 模型 active context 中只保留预算允许的历史
- SystemMessage 不被误删 / 当前用户消息不被删 / 最近历史优先

再次确认 **L2 ≠ 删除聊天记录**。

## 七、验证统一 Preflight（本阶段最重要验证）

构造：长聊天历史 + RAG context + previous_outputs + 较长当前 query，
让原始 Prompt 超过 input_budget。验证最终 `prepared_context_tokens <= input_budget`。

要求记录：before_tokens / after_tokens / saved_tokens / input_budget /
usage_ratio / 各层分别回收多少 token。

如果最终仍超过 budget：不能继续调用 LLM，必须走已有 overflow 降级逻辑。
检查 `context_budget_overflow_total` 是否增加。

## 八、验证 SSE 和前端

使用真实 `POST /chat/stream`，验证 meta/status/log/context/delta/done 顺序
不会因新增 context event 导致现有前端 parser 出错。

重点验证：
- context SSE 不应写入 chat_messages
- 刷新页面后 ContextNoticeBar 不应作为聊天消息重新出现（它只是 runtime UI）
- 前端普通模式不刷大量 L1/L2/L3 调试信息

同一轮多次 compact 建议聚合展示：「已优化上下文 · 本轮节省 300 tokens」。
如果当前实现已经很简洁，不必为这一点大改。

## 九、验证 Metrics

检查 `/metrics`：`context_compactions_total` / `context_tokens_saved_total` /
`context_budget_overflow_total` 存在且数值随真实请求变化。
label 只允许低基数字段（level/action/stage），禁止 session_id/user_id/turn_id/query。

## 十、MVP 验收门

完成上述验证后必须先得出 `MVP_PASS=true/false`。
只有 `MVP_PASS=true` 才能继续实现 L4。如果失败：先修复 L1/L2/L3/Preflight，
**不要带病继续实现 L4**。

## 十一、实现 L4 Context Collapse

目标：上下文达到一定比例时，把较早 active context 折叠成 Projection，
但不破坏原始 chat_messages。

L4 必须：零 LLM API 调用 / 可恢复 / 非破坏性 / 确定性。

### 触发条件

复用现有接口：

```
Context Preflight → 完成 L2/L3 → calculate_usage
→ usage_ratio >= CONTEXT_L4_TRIGGER_RATIO(0.80) → L4 Context Collapse
```

当前实际 input budget 约 7K，80% 比 90% 更合理。不要用「剩余 13K token」
这种大窗口模型规则。

### L4 不总结自然语言

L4 不调用 LLM，不要把 20 条聊天改写成"智能摘要"（那属于 L5）。
L4 做的是 Projection / Folding：

```
[Earlier conversation folded]

Fold ID: xxx
Messages: 8
Range: message_id 101-108

Key retained anchors:
- user request / assistant outcome metadata
- references required by later messages
- structured state if available

[Recent conversation]
...
```

不要生成虚构摘要。

### 折叠对象

优先折叠：旧的 user/assistant 普通历史。
不折叠：SystemMessage / 当前 UserMessage / 最近 N 个 turn / 必须的 domain state /
权限信息 / 长期 Memory SystemMessage / 当前 RAG evidence。

新增配置 `CONTEXT_L4_KEEP_RECENT_TURNS = 4`（如已有类似配置则复用），
加入 config 模块，不直接 os.getenv。

### Projection 数据模型

```python
class ContextFold(BaseModel):
    fold_id: str
    from_message_id: str | int
    to_message_id: str | int
    message_count: int
    original_tokens: int
    projected_tokens: int
    created_at: datetime
    reversible: bool = True
```

按项目实际 message id 类型调整，不要强行照抄。ContextFold 只描述哪些消息被
active context 折叠，原始内容仍在 chat_messages。

### Projection 内容（第一版保守）

```
[Earlier conversation folded]

8 earlier messages were omitted from the active prompt to stay within the context budget.

Message range:
101 - 108

The original messages remain available in session history.
```

若现有 metadata 已有 turn_id/role/timestamp/topic 可包含少量结构信息。
不为"好看的摘要"调模型——L4 目标是回收 token，不是理解整个历史。

### 可回滚

`restore_fold(fold_id)`：不是恢复数据库（数据库根本没删），而是下一次
active context projection 重新允许对应 message range 参与构建。
fold 状态只保存 fold ranges / projection metadata，不保存完整聊天快照。

### 状态放哪里

状态权威 PostgreSQL + LangGraph Checkpointer，优先复用。
禁止 context_snapshot.json / 本地文件 / 独立 SQLite / Redis-only 状态。
L4 Fold 状态若属于工作流当前状态，优先走现有 LangGraph state/checkpointer；
本阶段优先保持简单，暂不考虑独立 PG 表。

### 不破坏 Domain Graph 契约

CS/Travel 域图 `new_*_graph_input()` 只放本轮 input，历史由 Checkpointer merge。
L4 禁止：把整个历史重新塞进 graph input / 重写域图 state / 删除 domain state key。
L4 只作用 LLM Prompt Projection，不碰业务状态机（Confirmation/Handoff/Travel/Selection state）。

### L4 完成后重新 count

```
usage before → collapse → usage after
```

记录 before_tokens / after_tokens / saved_tokens / folded_messages / fold_id。

- 已低于 hard input budget → 正常继续
- 仍超预算 → 使用现有 deterministic hard trim

本阶段**不要触发真正 L5 LLM summary**。

### L4 SSE

```json
{
  "type": "context",
  "level": "L4",
  "action": "collapse",
  "before_tokens": 6200,
  "after_tokens": 4700,
  "saved_tokens": 1500,
  "reversible": true
}
```

用户端简单显示「已折叠较早上下文 · 节省 1,500 tokens」。Fold ID / message ids /
内部算法只进 developer log / trace。

### L4 Metrics

复用 `context_compactions_total` / `context_tokens_saved_total`（level=L4,
action=collapse）。可额外加 `context_fold_messages_total`，不必要就不堆。

## 十二、为 L5 补数据基础（不启用 LLM 摘要）

检查 `chat_sessions.summary` 与 `SessionMemory.summarize()` 数据结构。
若没有 `summary_through_message_id`，评估增加它——让未来 L5 知道摘要总结到了
哪一条消息，避免每次重新总结整段聊天。

建议未来结构：`summary` / `summary_through_message_id` / `summary_token_count` /
`summary_updated_at`。若 migration 风险较高，本阶段可以只写设计 TODO + 测试预留，
**不提前大改数据库**。

## 十三、当前禁止实现 L5

禁止：调用 DeepSeek 生成自动摘要 / 自动覆盖 chat_sessions.summary /
新增复杂 summary prompt / 删除被摘要历史 / 引入摘要质量 Judge。

原因：必须先收集 L1–L4 的真实回收数据。如果绝大部分请求在 L4 就恢复到安全范围，
L5 应该是极少触发的最后一道防线。

## 十四、新增 L4 测试

1. usage_ratio < trigger → 不 collapse
2. usage_ratio >= threshold → collapse
3. 最近 CONTEXT_L4_KEEP_RECENT_TURNS → 不折叠
4. 当前用户消息永远保留
5. SystemMessage 保留
6. 执行 L4 后 chat_messages 数量完全不变
7. fold → restore → active projection 可重新包含相应历史
8. after_tokens < before_tokens
9. 继续跑 `pytest tests/orchestration/graph/test_router_prefilter_order.py --no-cov`

所有局部测试继续 `--no-cov`。

## 十五、真实压力测试三类请求

| Case | 场景 | 预期 |
|---|---|---|
| A | 普通短问答 | L1/L3/L4 都不触发，无明显变慢 |
| B | 大工具输出 | L1 触发，可能 L3，L4 不一定 |
| C | 长对话 + 多工具 + RAG | 可能 L1/L2/L3/L4 全触发，prepared ≤ input_budget，回答正常 |

## 十六、性能要求

测 ContextBudgetManager 各阶段耗时（budget_count_ms / l2_ms / l3_ms / l4_ms /
total_context_budget_ms），都是本地逻辑，不应有秒级延迟。Trace 可记录，
Prometheus 不必全做 histogram，避免过度建设。

## 十七、提交规范

完成后 `git status` + `git diff` 确认其他会话文件没有混入。只 add 本任务文件
（路径限定 add + commit）。提交信息建议：`feat(context): add reversible L4 context collapse`。

## 十八、完成报告（A–G）

- A. MVP 实机验证（app/worker 重建、真实 input_budget、L1/L2/L3/Preflight/SSE/前端/metrics）
- B. 发现的问题（不要只写"测试通过"）
- C. L4 实现（插入调用链位置、触发条件、保留/折叠对象、ContextFold 结构、状态存放、restore 实现）
- D. Token 真实数据（before / L1/L2/L3/L4 saved / after / input_budget）
- E. 性能（Context Budget 额外耗时）
- F. 测试（命令 + passed/failed）
- G. Git（commit hash、修改文件、剩余脏文件归属确认）

## 十九、阶段完成后的状态

```
L1 Tool Result Budget   ✅      L2 History Pruning      ✅
L3 MicroCompact         ✅      L4 Context Collapse     ✅
L5 AutoCompact          ⏳ 下一阶段
Prompt Preflight        ✅      Context SSE             ✅
Frontend Notice         ✅      Metrics / Trace         ✅
```

下一阶段 L5 重点：token-based 触发 / 增量 summary / summary_through_message_id /
LLM 摘要失败回退 / 摘要事实保真 / 数字 SKU ID 关键实体保护 / 摘要版本 / 成本耗时监控。
本阶段不提前引入。

**最终原则：先实机证明 L1–L3 MVP 稳定，再加入零 API、可回滚的 L4；
等 L1–L4 的真实节省数据出来后，再决定 L5 应该多晚触发。**

---

## 附：L5 数据基础评估结论（2026-09-22 实施时回填，§十二）

**现状核实**：
- `chat_sessions` 仅有 `summary` / `context_summary` 两个 text 字段；
  无 `summary_through_message_id` / `summary_token_count` / `summary_updated_at`。
- `SessionMemory.summarize()`（backend/memory/session.py:40）：每次触发都重读
  最近 `SESSION_MAX_MESSAGES`(50) 条消息**整段重新总结**；触发条件仍是
  「条数 ≥ 50」（session.py:37 needs_summarization），与 token 无关。
- `MemoryService.end_turn` → `needs_summarization(session_id)`（repository 按
  消息条数判断）→ summarize → update_summary 落库。

**设计 TODO（下一阶段 L5 实施时做，本阶段不动数据库）**：
1. 新增迁移：`chat_sessions` 加 `summary_through_message_id bigint`（摘要覆盖到的
   最新 chat_messages.id）、`summary_token_count int`、`summary_updated_at timestamptz`。
2. `summarize()` 增量化：只取 `id > summary_through_message_id` 的消息做增量摘要，
   成功后推进 through 指针；失败保留旧摘要 + 旧指针（幂等可重试）。
3. `needs_summarization` 增加第二触发维度：`未摘要消息 token 数` 或
   `usage_ratio ≥ CONTEXT_L5_TRIGGER_RATIO`（复用 should_auto_compact）。
4. 关键实体保护：摘要 prompt 要求原样保留数字/SKU/订单号/金额（下一阶段细化）。

**本阶段决定**：不加列、不改 summarize——等 L1–L4 真实节省数据出来再定 L5 触发时机。
