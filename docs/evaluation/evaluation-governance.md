# 全项目功能评测与发布质量治理规范

> **全域唯一评测治理入口。** T0–T3 是工程测试层级，和 AI 质量 Golden/Suite/Live 评测不是同一件事。此文档规定评测资产版本、模块口径、结果有效性、准入阈值、发布门禁与回归追责；具体指标算法和默认阈值以 `backend/evaluation/release_gate.py`、suite manifest、Runner 及测试为准，**不在文档再造一套分数配置**。

## 1. 事实源与已有实现

- **模块清单**：`backend/evaluation/models.py` 的 `ModuleKind / MODULE_KINDS` 为统一评测框架的模块源；CLI/前端不能各复制列表。当前包含 `planner, rag, cs, sql, e2e, travel, travel-provider, travel-commerce, travel-booking`。记忆、Tool/Router、模型供应商及其它专项可有独立评测脚本，**不能假称都已接入 ModuleKind**。
- **数据集**：`backend/evaluation/datasets/<module>/` 常以 `cases.jsonl` 组织，具有版本治理的模块另提供 `manifest.json` 和可选的 `suites/<selection>.json`；使用 `backend/evaluation/dataset/loader.py` 和 `fixture_catalog.py`，不可随便复制一个新 loader。**目前 `travel-commerce` 与 `travel-booking` 只有 `cases.jsonl`，并无 manifest/hash 保护；loader 对缺失 manifest 存在兼容默认版本路径。这属于待补齐的评测资产缺口，不能宣称已锁版。** 历史 deprecated 文件不可进入新运行基线。
- **Runner**：`backend/evaluation/runners/`、`runners_config.py`；CLI `python -m backend.evaluation.cli`，实际选项先 `--help` 核对。离线评测、真实 Provider 探针和浏览器/API 端到端是不同覆盖层。
- **发布裁决**：`backend/evaluation/release_gate.py` 已区分 `PASS / FAIL / INVALID`。基础设施坏了、凭据缺失、没有有效数据属于 `INVALID`，不能当作“模型质量差”；必要指标缺失又不能以 0 条通过冒充 `PASS`。
- **样本校验**：`backend/evaluation/dataset/validator.py` 的某些分支仍只接受 planner/rag/cs，而模块注册已扩展到其它域。此为**需要定向审查的潜在契约漂移**；不得据此把所有新域数据标记合法或直接删除验证。

## 2. 统一评测资产规范

每个要进入发布判定的 Suite 至少固定：`module`、`suite_id/name`、版本/内容 hash、样本来源、`case_id`、问题及 expected/ground truth、适用环境、Runner 版本、模型与 Prompt 快照、检索 KB/fixture_set（若适用）、必需指标和阈值、有效最小样本数。样本集一旦进入正式基线，改样本/规则必须形成新版本，不能原地覆盖已使用的基线。Gold 数据含敏感信息时脱敏、控制访问与保留。

每条样本状态至少可归因 `pass/fail/error/skip`，运行再区分 `PASS/FAIL/INVALID`；**SKIP 不计为成功，INVALID 不作为质量回退结论**。与真实生产不同的 mock/provider 数据必须显示数据源和环境。LLM Judge 的评分还需固定 Judge 模型/Prompt/评分规则、人工校准和争议样本复核，避免“换了 Judge 却当模型质量进步”。

## 3. 按功能选择指标与风险用例

| 功能/模块 | 重点质量指标或断言 | 必须覆盖的失败/安全用例 |
| --- | --- | --- |
| Router / Planner | 意图准确率、多意图拆分、合法候选选型、澄清率、路由耗时 | 候选外 Tool、低置信度、domain handoff、提示注入 |
| RAG | Recall@k、MRR、nDCG、证据覆盖、引用准确/完整、拒答率与假回答率 | 无证据、跨 KB/租户、时效、上传失败、索引代次异常 |
| SQL | 意图→查询正确率、结果级断言、时间语义、授权表/行过滤、错误归因 | 注入、越权、跨租户、超时、只读/参数化、业务空结果 |
| CS | FAQ 命中、意图/槽位、工单/接管/确认状态机、正确拒绝、用户交付终态 | IDOR、错误退款、并发确认、转人工、业务 200+failed |
| Travel / Travel V2 | Brief 抽槽正确率、目的地/天数、可执行日程、约束保持、修改 diff/CAS | 12306 不可用、硬依赖 BLOCKED、真实 Provider、写入冲突/恢复 |
| Tool / MCP / Workflow | Schema 合法性、成功/empty/degraded/failed、重试与 fallback、成本耗时 | 跨域/候选外、SSRF、批准后参数篡改、重复写/未知结果 |
| Prompt / LLM | 模型回答质量、成本、版本指纹、发布后生效及回滚一致性 | 注入、越权、热更新并发、budget 阻断、备用模型能力不符 |
| Memory / Context | 召回准确、遗忘/更新、跨会话隔离、压缩忠实度及 token 预算 | 错用户记忆、旧事实覆盖、重要约束丢失、异步失败 |
| Evaluation / Admin / Frontend | 集版本一致、运行可恢复、数据指标真实、前端状态正确 | 未授权运行/发布、伪造 PASS、SSE 假成功、丢 Trace |
| Observability / Billing | 指标真实性、模型 token/账单对账、dashboard 查询有效 | No Data 冒充 0、泄漏 PII、失效告警未发送、价格漂移 |

**表格是选指标的目录，不代表所有模块当前已有统一 Runner 或上线阈值。** 每个模块的强制 Gate 和优先级由实际 `ModuleGatePolicy`、Suite 与对应安全契约决定。安全 P0 闸不以“综合平均分高”抵消。

## 4. 四层验证与准入决策

| 层级 | 测试来源 | 适用门禁 |
| --- | --- | --- |
| E0：Asset & Schema | dataset、manifest、hash、registry 校验 | 缺必要样本/真值/fixture → `INVALID`，拒绝开始 |
| E1：Offline / Unit | 固定数据与确定性 fake，算法/状态/安全测试 | 日常 PR 快速回归；不能宣称真实模型性能 |
| E2：Integrated / Live | 隔离 PG、Redis、真实 LLM/Embedding/Provider（按需） | 高风险契约、新模型/Prompt/检索策略发布 |
| E3：Product E2E | 实际网关、权限、三端 UI、实际 Provider、降级与恢复 | 发布/高风险迁移；截图和 Trace/API/DB 证据 |

T0–T3 的选择继续由 [测试策略](../development/testing-guide.md) 决定；E0–E3 描述评测的**证据层**，不要求每个 Bug 都运行所有层。只有运行有效、必要指标达标、安全红线全过，且没有阻断项才能输出发布 PASS。可用配置将低风险功能直接发布到本机，不强制影子双跑或灰度。

## 5. 生命周期与输出

1. **收录**：新增样本来自已修复回归、人工标注、生产脱敏失败案例或明确对抗集；有稳定 id、人工责任人、版本和质量审查。
2. **运行**：记录 `commit_sha`、数据集 hash、suite、模型/Prompt、路由策略、Provider、fixture_set、环境与 runner，以及 Trace/run_id、成本和持续时间。
3. **裁决**：必需指标/有效样本/安全断言缺失按 `INVALID/FAIL` 的实际含义判决；不能把 LLM 调用失败归类模型质量不合格。
4. **回归**：与同数据集/同规则基线比较并输出变化，发生阈值/算法/模型变化时说明不可直接对比的原因。
5. **发布**：管理员/授权人确认，记录判决、风险挂账、审批和回滚版本；高风险变更按 [发布质量](../operations/release-quality.md) 处理。
6. **维护**：旧测试由新契约替代且有覆盖证据才删除；数据集 `deprecated` 不能再用于新发布基线。

## 6. 收口验收（EV-01～EV-12）

| 编号 | 场景与通过标准 |
| --- | --- |
| EV-01 | ModuleKind、CLI、前端、Runner 注册与 manifest 一致；未知模块拒绝 |
| EV-02 | 缺字段、重复 case、坏 hash、未知 fixture_set 在开始前拒绝，避免污染数据 |
| EV-03 | Suite 版本/阈值、Prompt/model/commit 快照固定，历史运行不随新版本漂移 |
| EV-04 | 真实 valid 样本数不足、全部 skip、provider 不可用为 INVALID，不误报 PASS |
| EV-05 | 质量不达标为 FAIL；安全 P0 强制 FAIL/BLOCKED，不能靠平均分绕过 |
| EV-06 | RAG、SQL、CS、Travel 的指标按各自 policy 判，不强制用同一 RAG 指标 |
| EV-07 | 断点续跑/重复运行与 `run_id` 不造成双重样本或假完成 |
| EV-08 | 旧数据集弃用/替换有迁移说明与 Golden 断言，不无证据删测试 |
| EV-09 | 存储、队列、鉴权、租户及历史运行记录的读写权限通过 |
| EV-10 | 本地、CI 与真实 Live 报告区分，保留命令、环境、样本量与时间 |
| EV-11 | LLM Judge 模型/版本/人工抽查与评估成本可追溯 |
| EV-12 | 与发布/Prompt/模型切换正确关联，失败不可推断生产 READY |

标准化任务的下一轮审计需要从 `dataset/validator.py` 的模块范围、Release Gate 与 API 的历史记录一致性开始；报告明确 `已实现 / 部分实现 / 缺失 / 环境待验`，未实测项不能打勾。
