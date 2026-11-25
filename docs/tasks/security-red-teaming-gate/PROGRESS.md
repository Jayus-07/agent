# 系统级安全红队对抗与防越狱评测门禁 进度追踪

## 当前状态

`DONE_LOCAL_VERIFIED`。已在独立工作树 `D:\Program Files\workplace\agent-security-redteam`（分支 `feat/security-redteam-eval-gate`）完成全部实现与定向测试验证。

## 进展与交付证据

1. **统一评测模块扩展与单一事实源对齐**：
   - 在 `backend/evaluation/models.py` 将 `security` 纳入 `ModuleKind` 联合类型与 `MODULE_KINDS`；
   - 在 `backend/evaluation/report/builder.py` 补充中文展示名 `"security": "安全红队"`；
   - 在 `frontend-admin/src/components/observability/trace/AddToEvalModal.tsx` 同步声明前端模块列表；
   - `test_module_kind_single_source.py` 6 项断言全部 PASS，单一事实源守卫无漂移。

2. **安全红队基准数据集（E0 Schema 与锁版 content_hash）**：
   - 建立 `backend/evaluation/datasets/security/cases.jsonl`：包含 20 条经典对抗用例，覆盖 `direct_injection`（直接提示注入）、`system_prompt_leak`（系统提示词与凭据窃取）、`jailbreak`（越狱伪装模式）、`credential_leak`（数据库连接与 API Key 窥探）以及 `malicious_tool_call`（未授权删库与越权发信）；
   - 建立 `manifest.json`：锁定 `content_hash: "517e5b551cc2423e"`，防篡改；
   - 扩展 `backend/evaluation/dataset/validator.py`：新增 security 模块 attack_type、must_not_contain 非空断言、expected_behavior 校验；
   - `load_dataset('security')` 与 `validate_dataset` 校验通过，0 错误。

3. **安全 Runner 与判定引擎**：
   - 实现 `backend/evaluation/runners/security.py`：支持离线敏感词命中检测与 Live 模式安全防御判定；
   - 输出细粒度指标 `safety_pass` 与 `pass_rate`；
   - 在 `backend/evaluation/runners/builtin.py` 完成 Runner 注册与导出。

4. **Release Gate 安全一票否决门禁（Security Veto Policy）**：
   - 在 `backend/evaluation/release_gate.py` 配置 `_SECURITY_POLICY`，强制要求 `pass_rate >= 1.00` 与 `safety_pass >= 1.00`；
   - 验证一票否决生效：
     - 20/20 通过 (100%) -> `PASS`；
     - 19/20 通过 (95%) -> `FAIL`（普通模块 95% 可过，但安全红队强制一票否决）；
     - safety_pass 缺失或异常 -> `FAIL`。

## 测试验证记录

- `backend/tests/evaluation/test_security_redteam.py`: **6 passed in 19.56s**
- `backend/tests/evaluation/test_module_kind_single_source.py`: **6 passed in 22.83s**
- `backend/tests/evaluation/test_release_gate_module_aware.py` + `test_metric_label_single_source.py`: **43 passed in 19.39s**
- 合计定向验证 **55 项测试全部通过**，未修改主目录，未改动无关业务逻辑。
