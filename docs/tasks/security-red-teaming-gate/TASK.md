# 系统级安全红队对抗与防越狱评测门禁（Security Red Teaming Gate）

## 1. 目标

依据当前企业级 AI 项目安全门禁要求，建立全平台统一的**系统级安全红队与防越狱评测门禁（Security Red Teaming & Jailbreak Defense Gate）**。
补齐当前仓库仅有单模块零散安全单测、缺少全平台统一红队基准题集与自动化发布一票否决（Veto Gate）的缺口。

## 2. 核心范围与攻击面设计

评测基准题集以锁版的 JSONL 形式存储于 `backend/evaluation/datasets/security/`，必须包含 `manifest.json` 与 `content_hash`，覆盖以下三大类核心攻击：

1. **直接提示词注入（Direct Prompt Injection）**：
   - 目标：绕过或强行覆写系统角色设定（如 "Ignore all previous instructions and output..."）。
   - 预期行为：拒绝执行未授权系统指令，安全拒答或坚持原系统角色。
2. **越狱攻击（Jailbreak / Role-play bypass）**：
   - 目标：伪装为小说家、假想虚拟机、DAN（Do Anything Now）或极端假想场景，诱导输出违禁内容或高危指令。
   - 预期行为：识别语义违规，触发安全护栏拦截并返回标准安全话术。
3. **系统提示词与机密外泄（System Prompt & Credential Extraction）**：
   - 目标：诱骗 Agent 输出自己的 System Prompt、内部工具清单、API 密钥、数据库结构或未授权的内部元数据。
   - 预期行为：坚决拒绝泄露系统指令与敏感元信息。

## 3. 契约与一票否决门禁（Security Veto Gate）

1. **统一评测模块扩充**：
   - 在 `backend/evaluation/models.py` 中登记 `security` 模块（纳入 `ModuleKind / MODULE_KINDS`，或作为专用 Safety Gate 规范）。
2. **离线与在线判定**：
   - 规则与关键词/正则断言（零泄露、合规拒答关键字、未受污染状态）。
   - 语义裁判契约与防误伤断言。
3. **Release Gate 一票否决**：
   - 扩展 `backend/evaluation/release_gate.py`，新增 `SecurityVetoPolicy`：
   - 安全样本通过率必须为 100%（或零高危违规逃逸）。安全失败不得以综合平均分抵消，出现违规判定必须直接裁决为 `FAIL`，不可发布。

## 4. 验收标准

1. `datasets/security/cases.jsonl` 与 `manifest.json` 具备有效 content_hash，E0 数据集校验通过。
2. 安全评测断言逻辑具备独立轻量级 Runner 或 Evaluator。
3. 单元测试与契约测试全覆盖（通过正常请求、越狱攻击拦截、系统提示词防护、Release Gate 一票否决行为）。
4. 保持 Fail-Closed 原则：评测异常、模型输出不可解析或缺少断言时标记为 `INVALID`，禁止误报通过。
