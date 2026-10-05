# prompts/defaults —— YAML 默认模板（种子 / 降级兜底 / 灾备源）

> 双源定位（2026-10-06 提示词治理收口拍板）：**运行时权威 = DB
> `prompts.active_version` 指向的 `prompt_versions` 行**；本目录 YAML 只是
> 以下三件事的载体，**不是**运行时事实源。

## YAML 的三个用途

1. **首次种子**：app 启动 `init_prompt_service`（`app/server.py`）调
   `seed_defaults`——DB 无该 key 的 v1 时按 YAML 建版并置 active（幂等：
   v1 已存在即跳过，**改 YAML 不会更新已 seed 的环境**）。
2. **降级兜底**：DB 无行 / 无 active 版本 / 快照未就绪时，`PromptService`
   渲染回落 YAML（启动日志会打 `prompt_fallback=true`，属显式暴露非静默）。
3. **灾备恢复**：prompts 表数据丢失时，重启进程（种子路径）或管理端 seed
   端点（`POST /api/prompts` 系 seed）即可按 YAML 重建 v1。

## 改运行时模板的正确姿势

```
改 defaults/<key>.yaml（内容 + 变更注释）
→ python -m backend.scripts.promote_prompt_default --key <key>   # 建 DB draft
→ 管理端发布门禁：评测 → 审批 → 发布（激活 active_version）
→ python -m backend.scripts.gen_prompt_contract_lock             # lock 随变更提交
```

直接改 YAML 而不走 promote/publish = 只有新环境与降级路径生效，现网不变。

## 纪律

- 每个非 `code_controlled` 的 registry key 必须有同名 `default_file`；
  YAML key 字段 == registry key，模板变量 == spec.variables
  （`tests/prompts/test_registry_defaults.py` 全量校验 + 冒烟渲染）。
- 新增/改动 YAML 后跑：`pytest tests/prompts -q --no-cov`。
- 模板引擎固定 `str.format` 严格校验：`{var}` 占位、字面大括号写 `{{`；
  大段 JSON 示例外置代码侧（见 `backend/prompts/planner.py::build_output_example`），
  模板内尽量零 `{{` 转义。
- loader 发现「YAML 有 key 但 registry 未注册」会启动告警并跳过——这是
  断线信号（2026-10-06 借此发现 `customer_service.chat_fallback` 断线），
  见到就修，不许留着。
