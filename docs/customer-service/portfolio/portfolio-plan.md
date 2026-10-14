# 智能电商客服 · 作品集改造计划书

适用项目：Jayus-07/agent（客服域 `backend/customer_service`、用户端 `frontend`、坐席工作台 `frontend-cs`）　｜　目标：作品集展示　｜　编制日期：2026-10-14

## 一、目标与原则

**目标**：让面试官或读者在 10 分钟内看懂并复现：这个客服不只是聊天，而是「受控业务流程 + 可靠执行 + 人工协同」，并且每个设计取舍都讲得清楚。

**原则**：

- 不新增功能面，优先把已有能力讲清楚、跑得通、证据可复现。
- 只修一个真实的一致性问题（退款政策口径）。
- 模拟执行是有意的边界，诚实标注，不假装接了真实支付。

## 二、现状基线（已对照仓库核对，未运行测试）

| 能力 | 现状 | 位置 |
| --- | --- | --- |
| 多 Expert 编排 | 知识 / 查询 / 操作 / 投诉 / 转人工，Supervisor 调度 | `experts/`、`supervisor.py`、`graph_builder.py` |
| 确认状态机 | 待确认、取消、过期、执行中 | `confirmation*.py`、`state_machine.py` |
| 幂等与结果未知 | 副作用账本、`verifying` 状态、人工裁决收敛 | `reconciliation.py`、`backend/shared/idempotency` |
| 模拟执行与措辞防护 | 执行结果 `status=simulated`；composer 对模拟操作拦截「已退款 / 退款成功」类措辞 | `service/refund_service.py`、`response/composer.py` |
| 人工协同 | 派单 offer、在线状态、reaper、outbox、AI 推荐回复、坐席工作台 | `handoff/dispatch/`、`agent_assist.py`、`frontend-cs` |
| 演示沙盒 | `DEMO-` 订单、故障注入开关、`business-mock` 服务与接口契约 | `docs/customer-service/demo-sandbox.md`、`backend/mock/business_service/` |
| 验收与评测 | 40 个验收场景；约 300 条分类评测集（FAQ / 查询 / 操作 / 投诉 / 复合 / 安全）；租户隔离等测试 | `acceptance-scenarios.md`、`backend/evaluation/datasets/cs/`、`backend/tests/customer_service/` |

**真实缺口只有两类**：① 退款窗口口径不一致；② 缺少对外可读的设计说明、可复现的演示脚本和评测结果。

## 三、明确不做

- 接入真实支付 / 退款网关与状态回调。
- 运营质量看板、SLA 报表、AI 成本统计。
- 优惠补偿审批、主动进度通知。
- 为订单 / 物流 / 退款单独拆 Agent（它们是 Service，不是 Agent）。

## 四、任务清单

### T1　退款政策口径统一（唯一需要改代码的一项）

**问题**：`list_refund_candidates` 通过 `fetch_policy_window_days` 取窗口（RAG 检索后正则解析「N 天」，失败回退 7 天）来筛候选订单；但 `check_refund_eligibility` 只检查订单状态和是否已有退款，没有应用窗口。结果是候选列表和实际资格判断口径不同。

**做法（已选方案 A：结构化政策）**：

- 新增一份带版本号的退款政策配置（窗口天数、可退状态等），候选列表和资格判断都读同一来源。
- RAG 只用于解释政策并给出处，不参与放行。
- 写操作在政策不可用时拒绝，而不是回退放行。
- 资格结果带 `policy_version` 写入审计。

**涉及文件**：`service/refund_service.py`、`backend/config/customer_service.py`、`service/order_service.py`（`list_refundable_orders`）、新增 `backend/tests/customer_service/test_refund_policy_consistency.py`。

**验收**：

- 窗口外订单：候选列表中不出现，直接申请也被拒，理由一致。
- 修改窗口配置后，两处同时变化。
- RAG 不可用时，资格结果不变。
- 新增测试通过，原有退款相关测试不回归。

### T2　端到端演示脚本与录屏

**产出**：`docs/customer-service/demo-walkthrough.md`，README 客服章节嵌入 GIF 或截图（可复用 `gui-test-screenshots/`）。

**演示脉络**：

1. 「查我的订单」→ 展示 `DEMO-` 订单列表。
2. 「DEMO-1002 的耳机想退款」→ 出现确认卡片，展示金额与资格。
3. 连点确认 → 只执行一次（幂等）。
4. 按沙盒文档开启故障注入 → 进入 `verifying`，回复只说明「结果待核实」，不说已退款。
5. 运维裁决（`reconciliation.py` 注释指向的 `scripts/idempotency_ops.py`，使用前确认可运行）→ 状态收敛并留审计。
6. 「转人工」→ 坐席工作台认领，3 秒内出现 AI 推荐回复。
7. 坐席回复实时同步到用户端。

**验收**：陌生人按文档 10 分钟内能复现；全程界面不出现「已退款」；每一步有截图或命令输出；含数据重置步骤。

### T3　客服设计说明 + 评测结果

**产出**：`docs/customer-service/design.md`，建议章节：

1. 目标与边界（模拟执行、演示沙盒）
2. 分层：Supervisor / Expert / Service
3. 分工：LLM 理解与表达，规则引擎判定，RAG 提供解释与出处
4. 确认状态机（Mermaid 图）
5. 幂等、`verifying` 与人工对账
6. 人工接管与派单
7. 安全：租户隔离、输入输出门禁、措辞防护
8. 评测方法与结果
9. 设计取舍与未做之事

**评测**：运行 `backend/evaluation` 中的客服数据集、`backend/tests/customer_service`、`scripts/cs_p0_acceptance.py`，把命令、日期、commit、模型和通过率写成表，失败样例如实列出，不美化。

**验收**：任何读者拿着文档中的命令能复现同一张结果表。

### T4　局限与生产化路径

**产出**：design.md 末节与 README 各一段简短说明。

- 当前执行为模拟（`status=simulated`），措辞层已防止误报成功。
- HTTP 网关模式下本地退款查重被跳过（代码注释已标为能力边界），生产需要以业务网关为权威数据源。
- 生产化路径：执行适配器接口、权威查重、业务回调对账、资源级授权测试。
- 可选小改动：把 `_simulate_execute` 的分发抽成执行器接口，仅保留模拟实现，用于展示替换点（不接真实服务）。

**验收**：读者能明确区分「已实现」「模拟」「未实现」三类。

## 五、总验收清单

| 项 | 现状 | 本计划动作 |
| --- | --- | --- |
| 自然语言理解与多诉求拆分 | 有评测集（复合类） | T3 展示评测结果 |
| 多轮上下文与取消 | 有验收场景 8、9 | T2 演示涉及 |
| 身份与租户隔离 | 有 `test_cs_tenant_isolation` | T3 写入安全章节 |
| 政策一致性 | **有缺口** | **T1** |
| 确认与幂等 | 已实现 | T2 步骤 3 |
| 结果真实性（不把模拟说成成功） | composer 已拦截 | T2 步骤 4、T4 |
| 故障降级与不编造 | 有故障注入与拒答场景 | T2 步骤 4 |
| 人工协同 | 已实现 | T2 步骤 6、7 |

注：「现状」列来自对代码和文档的阅读，未逐项运行验证，T3 的评测运行即是验证。

## 六、建议顺序

T1（修口径，半天到一天）→ T3 的评测运行（先拿到真实数字）→ T2（录演示）→ T3 / T4 的文档收尾。这样演示和文档里引用的都是修复后的真实结果。
