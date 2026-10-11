# 文档内容发送至模型的边界

上传文档的元数据抽取可能把正文采样和文件名发送至当前配置的模型 Provider。修改解析、采样、模型出口或 Trace 留存时，必须核对以下内容：

- 采样长度、PII 处理和文档类别策略以 `backend/config/`、RAG metadata pipeline 与对应测试为准；不得因启用级联路由而绕过脱敏或授权。
- 用户身份头、租户标识和会话历史不作为文档抽取内容传给 Provider；日志不得记录完整 prompt、密钥或文档正文。
- 财务、合同、客户数据等敏感类别要核实脱敏、Provider 使用条款和 Trace 留存期限。供应商的数据留存/训练条款未完成安全与采购确认前，不得把系统描述为已满足相关合规要求。
- Trace 清理由 `backend/observability/trace_retention.py` 实现，期限和敏感类型由配置提供；配置、清理逻辑或数据分类变化时更新相应测试。

当前真实数据流、运行开关及留存值以代码、部署配置和 Provider 合同为准；本文件记录核查边界，不保存审批过程或历史运行快照。

## 威胁模型、边界与最小权限

**输入不可信**：聊天输入、网页检索结果、RAG 上传文档、MCP/Tool 返回值、Provider 返回内容、截图或引用文本，都属于低信任数据。即使包含“系统指令”“开发者规则”“管理员口令”，也不能提升为系统级指令或合法授权。Guardrail 能降低误用，**不能替代 API 的资源级 AuthZ 和 Tool Governance**。

| 威胁 | 必须防范的失败路径 | 验收方式 |
| --- | --- | --- |
| Prompt Injection / 指令洗白 | RAG/MCP 文档提示忽略规则、泄露密钥、绕过 Tool | 对抗用例注入到低信任文档，权限/Tool 结果不变 |
| 敏感信息泄漏 | LLM prompt、Tool args、Trace detail=full、日志/导出出现用户 PII/密钥 | 脱敏成功/脱敏异常分支/导出和 Trace API 核对 |
| MCP/Provider SSRF | 任意 base_url、重定向、内网地址、DNS 变化与带密钥出站 | 现有 URL Guard / Admin 约束 / 探测速率与日志验证 |
| 越权业务副作用 | 非授权用户利用 Prompt 诱导退款、删库、改价、转账 | 403、资源级拒绝、审批/确认与幂等账本验证 |
| 知识库污染 | 跨部门上传、标题伪造可信来源、引入恶意外链、引用捏造 | 上传分类/审核、来源证据、KB 权限、引用核对 |
| 不受控外发 | 财务/员工/客户数据发到未经批准供应商或跨区域 | 模型供应商范围、最小内容采样、凭据与数据保留政策检查 |
| 输出与 HTML 注入 | 返回 Markdown/HTML 脚本、URL、SQL 片段被 UI 无条件执行 | XSS/安全渲染、SQL 只读验证，敏感操作仍需审批 |

### 开发守卫

- **信任传播**：身份、租户、系统指令、允许的 Tool 候选、关键业务条件，只能来自受控的服务端状态与现有规范。LLM 提议可解释，不可替代模型外验证。
- **最小数据**：上传文件的正文采样、检索 chunk、长记忆、SQL 结果在进模型前做合法范围/PII 筛选。未经来源或用户授权不得通过另一 Provider fallback 把敏感信息送到新目的地。
- **安全失败优先**：身份/权限、审计必需账本、不可逆写操作确认不可用时拒绝执行；RAG 证据不足可拒答或澄清，而不是捏造引用。
- **成本与日志**：拒绝、脱敏失败、Provider 风险和 Tool Blocked 留最小可审计事件；禁止记录 token、API Secret 明文或完整敏感 prompt。观测点参考 [Trace 规范](../observability/trace-model.md) 与 [RBAC](../auth/authorization-standard.md)。
- **异常消息防泄漏（2026-10-11 已实现，`mcp_servers/manager.py`）**：工具异常的 `str(e)` 可能携带服务器路径、用户内容或密钥，异常类型不能作为安全凭据——MCP 边界只透显式受控异常 `MCPToolError`（`safe_message`+`code`，构造时声明安全），其余异常对调用方只回稳定错误码+通用文案+trace_id；日志不记录异常消息原文与堆栈文本，只记类型+发生位置+trace_id。受控异常的 `safe_message` 同样禁止内插任何 `str(e)`（含框架自有异常，如 RagAuthorizationError 会包裹底层异常文本）。
- **MCP 协议通道执行主体（已实现，`mcp_servers/protocol_app.py`）**：API Key 是服务凭据，不自动映射成用户身份；标准 MCP 端点的工具执行绑定显式受限机器 principal（`svc:mcp-protocol`，角色 `service_workflow` 只含 `sql.read`），私有诊断工具（如 get_stats）对该通道拒绝。REST/Java 通道分别经网关身份头与 internal-token+actor 委托；Local Tool 与 MCP Tool 共享同一业务权限边界，Local+MCP 混合架构保持不变。

**当前证据边界（补测项）**：AI-SEC-04 的 SSRF 重定向/DNS 变化对抗、AI-SEC-05 的私有文档跨 Provider fallback、AI-SEC-08 的对抗评测集与发布门禁尚不能凭源码宣称 PASS；须建立定向用例与运行证据。其他条款也应遵循静态与实测分级。

### AI-SEC-01～AI-SEC-08 验收

1. RAG 文档和 MCP 返回包含“忽略系统规则”时，Router/授权/工具候选仍受服务端守卫。
2. 不合法 Tool、未授权写入、跨 tenant/owner 的操作不能由 Prompt 绕过。
3. Trace/日志/评测导出中 PII 脱敏在正常和异常分支有效；缺少证据时标 BLOCKED。
4. 私有 Provider 探测、URL 重定向和动态 DNS 不能绕过出站 SSRF 策略。
5. 用户明确禁止分享的文档或个人记忆不会经备用模型泄漏。
6. XSS/恶意链接等模型文本在 Web/Admin/CS 均安全渲染。
7. 敏感模型/Prompt 版本、价格/配置变更、发布操作有可信操作者与审计。
8. 安全对抗集与 [全域评测规范](../evaluation/evaluation-governance.md) 关联；没有真实攻防实验不能宣称安全已通过。
