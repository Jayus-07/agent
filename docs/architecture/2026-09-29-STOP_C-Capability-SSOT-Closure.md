# Architecture Simplification — STOP C Capability SSOT Closure

日期：2026-09-29

## 结论

Capability 的业务 metadata 已收口到唯一作者源：
`backend/orchestration/router/capabilities.yaml`。

每个 Capability 现在在 manifest 中声明：name、Skill executor binding、domain、description、params_schema、planner_examples、routing examples、risk_level、routed、fast_path 与规则路由 metadata。所有路由派生量、Planner schema 与 Skill 兼容属性均从此文件启动期派生。

## 运行时边界

```text
capabilities.yaml (CapabilitySpec SSOT)
  -> manifest.py (fail-fast CapabilityDecl)
  -> skills.metadata.bind_manifest_metadata()
       -> Skill legacy properties / per-capability validation metadata
  -> capability_registry.CAPABILITY_SCHEMA (Planner/Critique schema)
  -> router types / vector router (routing projections)
```

Skill 仅保留 `name`、Tool dispatch、timeout/retry、参数校验调用、输出归一化、trace 与错误处理等执行 runtime。`BaseSkill` 的旧 `capabilities`、`description`、`params_schema`、`examples` 属性仍被启动期投影，避免破坏 Planner、graph、evaluation 与管理端的既有读取路径；它们不再允许由 Skill 类作者维护。

多 Capability Skill 的执行期参数校验按 `capability_metadata[capability]` 读取 manifest schema，不再依赖类级重复 schema。

## 迁移核对

- 17 / 17 Capability 均有非空 description、params_schema 与 planner_examples；
- 12 / 12 Skill 由 manifest binding 取得兼容 metadata；
- `backend/skills/*/skill.py` 不再声明 capabilities、description、params_schema 或 examples；
- `ToolRegistry.CAPABILITY_SCHEMA` 改由 manifest 派生，保留 `get_schema()`、`get_node()`、Planner prompt API；
- `@tool` 的 name、args_schema、description 仍是 Tool 契约的唯一作者源；MCP 的 RAG/SQL schema 继续从 Tool args_schema 派生。

## 保护边界

本 STOP 未修改 Tool 输出、Tool args_schema、MCP 调用路径、RAG/SQL 算法、Authorization、Idempotency、数据库 schema、LangGraph node id、checkpoint、SSE 或前端 API。

## 验证

```text
实施会话（先 RED 后 GREEN）：
  test_capability_runtime_metadata_is_declared_in_manifest
  RED: CapabilityDecl 缺 description，1 failed -> GREEN: 1 passed
  Registry / layer / ADR guards: 37 passed in 23.38s
  Skill + Planner + Router compatibility: 105 passed in 21.18s

交接复验（2026-09-29 接管会话，代码冻结下新鲜运行）：
  四个契约门 registry/layer/adr0001/base_output:  45 passed in 29.12s
  tests/skills + tests/orchestration + tests/evaluation:
    906 passed, 112 skipped, 0 failed（串行 28:51；3 个 warning 为
    evaluation TestCase 命名的既存 PytestCollectionWarning）
  tests/api/test_registry_overview_api.py + test_audit_fixes.py
    + test_map_lookup_skill.py:  35 passed in 11.29s
  全仓收集冒烟: 7489 tests collected，无导入错误
  py_compile: 17 个改动 .py 全部通过
```

live planner 评估（真实 LLM，`evaluation/datasets/planner_params.json`）本轮未重跑：
本次是「作者源迁移」而非「值变更」，17 个 capability 的 description /
params_schema / planner_examples 原样迁入 manifest，派生路径由契约门
`TestSkillToolAlignment`（manifest params ↔ Tool 实际参数不脱节）守护。
