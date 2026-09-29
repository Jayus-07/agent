# Architecture Simplification — STOP G 实施结案：Tool Contract Closure

日期：2026-09-29
基线：`main@094d307`（STOP G 准备审计同仓发布）
性质：实施结案报告。设计依据 = `STOP_G_Preparation_Audit.md`（含设计修正裁决），本报告只记录落地事实与验证证据。

结论先行：

```text
TOOL_CONTRACT_PASS=true
TOOL_RUNTIME_TOUCHED=false
TOOL_FUNCTION_REWRITE=false         # 34 个 Tool 函数体零修改
MCP_TOUCHED=false
STEP_RESULTS_TOUCHED=false          # StepResult/f11/SSE/前端 sources/域图 reporter 零 diff
STOP_G_PASS=true
```

---

## 1. 设计修正说明（为何不是「统一 ToolResult」）

原始口号「所有 Tool 统一 ToolResult」经审计否决，实施遵循修正后的**两型契约**：

1. `ToolResult` 名字与语义已被冻结的 `core/tool_runtime/models.py` 占用——那是**执行治理**结果（timeout/retry/熔断/error mapping），不是业务输出。把它复用为业务契约会把两个正交概念焊死。
2. 18 个 text 型工具（E8 存量）的输出是给 LLM 读的 Markdown，强套封套只会让 prompt 变胖（`shared/tool_envelope.py` 模块注释开宗明义反对）。
3. `sql_query_tool`（成功=Markdown 给 LLM）与 `execute_sql_tool`（封套给程序）的「风格分裂」实为**受众分裂**，是对的。

最终契约形态：

```
Tool（@tool，输出二型之一，类型显式声明）
  ├─ text 型       → Markdown/纯文本 str（给 LLM 读，18 个）
  └─ structured 型 → {"status","data"} 封套 JSON str（给程序消费，16 个）
        ↓  Skill 边界（BaseSkill._normalize_output，structured 声明经 unwrap_envelope 解包）
step_results[].output = 干净业务载荷（成功=裸 data；失败=failed 步骤）
        ↓
Reporter（只见到 kind 干净的输出，永不见封套）
```

统一的是 **Tool Contract Boundary**（声明 + 边界 + 失败语义），不是 Tool 实现，也不是返回类型。

## 2. 修改范围（M1-M5 全量落地）

| # | 动作 | 文件 | 内容 |
|---|---|---|---|
| M1 | 全部 Skill 显式声明 output_type | 9 个 skill.py（rag/report/email/data_export/web_search/web_crawl/data_collection/competitor_analysis 补 `text`；**map 改 `structured`**） | 12/12 Skill 全部显式（sql/business_analysis/travel_poi 原有），禁止隐式默认；留在 Skill ClassVar 不迁 capabilities.yaml（声明是解析行为非业务 metadata） |
| M2 | 解包器收敛 | `shared/tool_envelope.py` 新增 `unwrap_envelope(raw)`；`orchestration/workflow/skill_adapter.py::call_sql` 手写 json.loads+status 判断迁移至该出口 | 语义：success→data；failed→封套整体保留（失败语义由调用方决定）；非封套/无 status→原样。skill_adapter 对无 status 历史形态维持旧「解析后透传 dict」 |
| M3 | 失败语义对齐 | `skills/validation.py::validate_semantics` | `status=="failed"` 成为失败判定第一等依据（含 NOT_FOUND 话术分类）；error 键嗅探保留为历史形态兼容兜底。`ToolResult.status` 执行态语义零触碰 |
| M4 | 裸 JSON 透出修复 | `skills/base.py::_normalize_output`（structured 双入口：str 解析后与 dict 直入都经 unwrap）；`skills/map/skill.py` 声明 text→structured | map.lookup direct 路径：真实封套经边界解包，成功=裸业务 data dict，失败=failed 步骤；封套不再可能到达 `_coerce_final_answer`/Reporter。travel_poi 输出由封套 dict 升级为裸业务 dict（与下游 `_build_data_summary`/`columns+rows` 判定的既有设计对齐，审计 §2.1 已预判该不一致） |
| M5 | 契约守卫测试 | `tests/skills/test_output_type_declarations.py`（12 Skill 声明显式性 + 声明≡Tool 模块输出形状静态检测）；`tests/skills/test_tool_contract_boundary.py`（unwrap 单元 + 边界四象限 + M3 status 优先 + skill_adapter 解包）；`tests/test_map_lookup_direct_contract.py`（M4 实链路验收） | 见 §4 |

**13 个生产文件 + 3 个测试文件，净改动约 220 行；34 个 Tool 函数体、`core/tool_runtime`、MCP、`step_results`/`StepResult`/SSE/前端、域图 reporter 零 diff。**

## 3. 未修改组件（禁改红线核验）

- `core/tool_runtime` 九件套：零 diff。executor 透传语义与 `ToolResult.status` 执行态语义不变——业务失败识别仍收敛在 skill 层，执行态/业务态保持正交。
- 34 个 Tool：零 diff（含 `sql_query_tool` 混型挂账，见 §6）。
- MCP：零 diff。三出口（REST /api/mcp、8091 标准协议、internal_ai）与 `manager.route` 对外信封原样。
- `step_results`/`StepResult` TypedDict（orchestration/state.py）、f11 失败留痕、SSE 帧序、前端 sources 提取、四个域图 reporter：零 diff。
- 合法旁路（SQL dataclass 协议、CS `execute_sql_struct`、travel 专家函数直调）：零 diff，未纳入契约。

## 4. 测试与验证记录（解释器 .venv Python 3.10.2，PGPORT=5433，`--no-cov`）

| 批次 | 结果 |
|---|---|
| M5 新测试（声明守卫 + 边界行为 + map direct 实链路，首批 6 文件批次） | **40 passed** |
| `tests/test_tool_envelope.py` + `tests/skills/test_base_output_contract.py`（既有契约门） | 全绿（含 119 批次内） |
| `tests/skills` 全量 + envelope + map 离线回归 | **119 passed** |
| 契约四件套（registry/layer/adr0001/base_output） | **45 passed** |
| `tests/orchestration` + `tests/runtime` 全量（含 direct/workflow 执行器、checkpoint 恢复矩阵含 poi_search direct 路由） | **757 passed**（28 分 17 秒） |

**M4 真实验证（spec 要求）**：`test_map_lookup_direct_contract.py` 三用例走全真实链路——
①成功路径：覆盖 conftest 的 LBS 切断（fixture 注释明确允许）+ 仅 HTTP 边缘罐头响应，真实 `map_geocode_tool` 返回真实 `ok()` 封套 → 真实 `MapLookupSkill.execute` → 真实边界解包 → 断言 output 为裸 data（无 `status` 键）→ 真实 `_coerce_final_answer` 产 final_answer 且不含 `"status"`；
②未配置路径（conftest 默认切断）：真实 `not_configured()` 失败封套 → failed 步骤，final_answer 无封套；
③「查不到」路径：真实失败封套 → failed 步骤。

## 5. Runtime 兼容证明

1. **既有契约门全绿**：`test_base_output_contract.py` 8 用例（text/structured/按 capability 覆盖）零修改通过——M4 的解包仅命中封套形状（status+data/failed），非封套 JSON（如 `{"summary":"洞察"}`）与全部 text 行为逐字节保持。
2. **声明守卫**：12 Skill 声明显式性 + 声明≡Tool 模块形状静态检测（封套出口特征正则）全绿，防漂移门生效。
3. **消费方保旧**：skill_adapter 无 status 历史形态维持「解析后透传 dict」（测试锁定）；reporter/前端/事件流零改动。
4. **失败语义双保险**：status 第一等 + error 键兜底，旧形态（裸 error dict）与直调场景（str 形态 success 封套）均不误判。

## 6. 遗留项

- **`sql_query_tool` 混型挂账**（tools/sql.py:147-153，成功=Markdown/失败=封套）：按两型契约应整体 text 型，失败宜改 `[SQL ERROR]` 文本。属 Tool 函数体修改（本 STOP 禁改），留待独立卫生批次；其消费方（graph-bind LLM/MCP）现状可用。
- **semantic 失败的 step 级 error 为粗口径**（「输出校验失败: semantic」，可读 reason 在 ValidationFailure.envelope.details 经日志承载）：改动前后同径的既有行为，未在本 STOP 扩面；若要 step 级携带 reason 属 error 呈现增强，独立评审。
- **email 三件套 agently CLI JSON 形状**：登记为 text 型内嵌结构（外部 CLI 信封，非本平台封套），不改造。
- **T25 冻结集漏 `qweather_backup`**（STOP F 期间发现，f4f9628 遗留）：与本 STOP 无关，仍待该线补一行。

## 7. 最终判定

```ini
TOOL_CONTRACT_PASS=true
TOOL_RUNTIME_TOUCHED=false
TOOL_FUNCTION_REWRITE=false
MCP_TOUCHED=false
STEP_RESULTS_TOUCHED=false
STOP_G_PASS=true
NEXT=STOP_H  # Architecture Final Documentation，不再引入新抽象
```

最后验证：2026-09-29（本报告第 4 节全部命令实跑）。
