# Architecture Simplification — STOP G 准备审计：Tool Contract Closure

日期：2026-09-29
基线：`main@c2dc0a4`（STOP F 已结案，四标志全 true）
性质：**只读审计，未修改任何生产代码**。证据 = 4 路并行深扫（tools 全量 / 消费方链 / reporter / MCP）+ 主线自查交叉验证。

结论先行：

```text
STOP_G_READY=true
NEXT_ACTION=IMPLEMENT_STOP_G
SCOPE=两型输出契约显式化 + 边界归一化收敛 + 失败语义对齐（非 Tool Runtime 重写）
TOOL_RUNTIME_REWRITE=false
TOOL_MASS_REWRITE=false            # 34 个 Tool 一个都不重写
MCP_MIGRATED_TO_INTERNAL=false     # MCP 确证为纯 Integration Adapter，维持现状
```

**审计对原始目标做了一次设计修正（本节即「发现需重新设计先停下」条款的产出）**：
「从 dict/str/markdown/json 统一为单一 ToolResult」**不成立，也不应该做**——
① `ToolResult` 这个名字已被冻结的 `core/tool_runtime/models.py` 占用且语义不同（那是**执行治理**结果：timeout/熔断/重试；不是**业务**结果）；② 18 个 Markdown 工具的输出是给 LLM 读的，强套封套只会让 prompt 变胖（`shared/tool_envelope.py` 模块注释开宗明义反对）；③ `sql_query_tool`（成功=Markdown 表格给 LLM 读）与 `execute_sql_tool`（封套 JSON 给程序消费）的「风格分裂」实为**受众分裂**，是对的。

修正后的目标形态：

```
Tool（@tool，输出二型之一，类型显式声明）
  ├─ text 型       → Markdown/纯文本 str（给 LLM 读，E8 存量 18 个）
  └─ structured 型 → {"status","data"} 封套 JSON str（给程序消费，16 个）
        ↓  Skill 边界归一化（既有 _normalize_output + 收敛后的解包器）
step_results[].output = 干净业务载荷（成功=业务 dict/文本；失败=status=failed 步骤）
        ↓
Reporter（只见到 kind 干净的输出，永不见裸封套）
```

即：**统一的是契约的声明与边界，不是把 34 个 Tool 改成一个返回类型。**

---

## 1. Tool 输出类型盘点（34 个 @tool）

总数 34（与 `tools/tool_registry.py:11` 注释、四层规范口径一致），分布 **16 envelope-json / 18 markdown-plain-str / 0 other**，与四层设计规范 §E8 台账（16/18）**完全吻合，零漂移**。

### 1.1 structured 型（16 个，走 `shared/tool_envelope.py` 封套）

| 工具 | 位置 | 备注 |
|---|---|---|
| map/ 全部 14 个 | `tools/map/{geo,place,route,static_map,street_view,weather,lookup}.py` | 统一经 `map/_base.py` 的 `ok/fail/not_configured` 出口；「查不到(ok count=0+note) ≠ 查不了(fail)」已分开 |
| travel_poi_search_tool | `tools/travel/poi.py:130` | 直接用 tool_success/error_result；查不到城市 fail 附 known_cities |
| execute_sql_tool | `tools/sql.py:64` | 封套；⚠️ 意外异常分支 `raise`（sql.py:126）绕过封套出口（E8 已登记） |

### 1.2 text 型（18 个，E8 例外存量，输出给 LLM 读）

| 工具 | 位置 | 备注 |
|---|---|---|
| sql_query_tool | `tools/sql.py:129` | **成功=Markdown 表格（sql.py:147）**；失败却返封套（:149-153）——唯一混型工具，见 §5.3 |
| memory_search / memory_store | `tools/memory.py:34,65` | 纯文本 |
| send/search/read/watch_email（4 个） | `tools/email.py` | 成功体是 agently CLI 自有 JSON（**第三种形状**，非封套）；失败 `[AGENTLY ERROR:n]` 文本 |
| export_csv_tool | `tools/export.py:12` | `[EXPORT FAILED]`/成功文案 |
| generate_report_tool | `tools/report.py:25` | Markdown 报告 |
| search_knowledge_tool | `tools/rag.py:12` | `pipeline.ask() -> str` 纯答案文本 |
| web_search / web_crawl | `tools/web.py:29,144` | Markdown；失败一律 `raise`（有意，交 Skill 重试） |
| data_collection_tool | `tools/data_collection.py:21` | Markdown 采集报告 |
| competitor_analyze/watch/history/watchlist（4 个） | `tools/competitor.py` | Markdown；失败 `raise` 交重试 |
| calculate_tool | `tools/calculator.py:114` | `expr = result` / `❌ 无法计算` |

### 1.3 registry 与声明机制现状

- `tool_registry.register()` 只存 name/source_file/args schema，**不记录输出类型**（tool_registry.py:43,98-108）。
- **两型声明机制已存在**：`BaseSkill.output_type: ClassVar[str]`（默认 "text"）+ `output_types: dict` 按 capability 覆盖（skills/base.py:145-146,203）。显式声明者：`business_analysis`/`sql`/`travel_poi` = structured；`map` = text（显式）。**其余 skill 未声明（隐式 text）**——声明覆盖面是收口点之一。
- 位置争议（G2）：output_type 目前在 Skill 类代码里，不在 capabilities.yaml。审计裁定：**维持在代码**——它是解析行为（决定边界怎么处理返回值），不是业务 metadata；与「Skill 节点自注册」同层。收口动作 = 补齐声明 + 加一致性测试，不是搬家。

## 2. 消费方与 Normalizer 现状（核心发现：契约在边界上是「半成品」）

### 2.1 归一化层只有半层

- **边界归一（已有，有测试门）**：`BaseSkill._normalize_output`（skills/base.py:192-234）按 output_type 处理：text 型非 str → json.dumps（防 dict 炸下游，有事故记忆）；structured 型 str → `json.loads` 为 **完整封套 dict（不解包 data）**，失败保持原样仅告警。回归门 = `tests/skills/test_base_output_contract.py`（8 用例）。
- **失败识别（错位）**：`validate_semantics`（skills/validation.py:288-312）判**「有无 error 键」**而非 `status=="failed"`——封套里声明的 status 字段在失败判定中没被读到，靠 error 键嗅探兜底。tool 返回 failed 封套时 `ToolResult.status` 仍是 SUCCESS（见 2.2），业务失败全靠这层嗅探转成 step failed。
- **封套解包（散落）**：全仓生产代码「读 status 取 data」**仅 1 处**——`orchestration/workflow/skill_adapter.py:100-107`。`parse_tool_envelope`（shared/tool_envelope.py:73）**生产零调用**。
- **绕过封套的旁路（合法，非收口对象）**：SQLSkill 直调 `agent.ask_struct` 消费 dataclass（skills/sql/skill.py:251-260，step 间协议 SQLResult/BusinessInsight）；CS 服务层直调 `execute_sql_struct`（进程内服务调用，非工具消费）；travel 专家直调 Python 函数（域内组装）。

### 2.2 执行层零参与（冻结确认）

`core/tool_runtime/executor.py:180-190`：tool 返回 str **原样塞进 `ToolResult.data`**，不 parse 不看内容；异常才经 error_mapper（:209-221）产失败 ToolResult。⇒ **`ToolResult.status`（执行态）与封套 `status`（业务态）是两个正交概念**，现状靠「executor 透传 + skill 层嗅探」拼接，这就是契约需要收口的根因。

### 2.3 缺陷实证：裸封套 JSON 会直透用户

`map.lookup` 在 direct 支线用户标签表中（direct_executor.py:74）→ map skill 声明 text → 封套 JSON str 作为 output 直通 → `_coerce_final_answer`（direct_executor.py:306-307）**str 原样成为 final_answer**。即 direct 命中地图能力时，用户看到的是 `{"status": "success", "data": {...}}` 原始 JSON。reporter 侧同样：单步骤 str 透传路径（reporter.py:184-189）无 JSON 检测。（实施期需实机复验该路径的可达性，但代码链路已闭合。）

## 3. Reporter 输入盘点

### 3.1 主图 reporter（agents/reporter/reporter.py）

- 输入契约：`step_results: dict[step_id → StepResult]`（权威 TypedDict 在 `orchestration/state.py:48-62`：step_id/capability/status(pending|running|success|failed|skipped)/output(Any)/error/row_count/is_empty/error_type...）。
- **output 类型分发**（generate_final_answer，:112-271）：str → RAG 快速透传（:161-179）/单步骤直返（:184-189）/`str()` 进 prompt（:411）；dict → 零 LLM 模板渲染（columns+rows 表格 :490-513、summary+risks 洞察 :516-544、`_build_data_summary` 只吃 dict :554）；其他类型 → `str()` 强转兜底，**不崩**。
- **对 JSON 字符串零感知**：reporter 内 grep json 零命中——封套 JSON 在 reporter 眼里是哑文本（解析只发生在上游 skill 边界，且仅 structured 声明者）。
- 失败呈现：`_is_step_successful` 五重判定（:289-309）；全失败 → 「抱歉」降级块（:134-155）；混合 → `❌ 失败` 行（:421-422，⚠️ 原始 error 全文进 prompt）。
- ⚠️ 容器/元素**零容错**：`state.get("step_results", {}).items()`（:43,131）非 dict 即崩——对照 trace_middleware 有 `isinstance(sr, dict)` 守卫（trace_middleware.py:184）。

### 3.2 四个域图 reporter 全部没有 step_results 概念

| 域 | 输入 | 证据 |
|---|---|---|
| CS | `last_expert_result` dict（ExpertResult） | customer_service/reporter.py:17-71；CSGraphState 无 step_results 字段 |
| 选品漏斗 | 自有 state（candidates/stage_logs/pool） | selection_funnel/reporter.py:511-539，纯模板渲染 |
| 旅游主 | brief/itinerary/validation 三产物 | travel/reporter.py:57-303 |
| 旅游 commerce/booking | typed 对象（CommerceResult/ExecutionOutcome） | travel/commerce/reporter.py:25-63、travel/booking/reporter.py:22-27 |

⇒ **step_results 是主图 plan/direct/workflow 专有契约**；「Tool→ToolResult→Reporter」链只在主图成立，域图各有自己的类型化交付契约（STOP F 已冻结）。STOP G 不得向域图扩散。

### 3.3 workflow 支线与下游

- workflow_executor 产出同 schema 单条目（key=`workflow_{name}`，output 恒为拼好的 Markdown str，direct_executor.py:401-410）。
- 前端**不消费 step_results 原始结构**（grep frontend/ 零命中）：done 事件的 sources 由服务端 `_extract_sources_from_steps` 预提取（events.py:369-374），步骤展示走 log 事件。final_answer = 纯 Markdown（token 流剥 META 注释）。
- `_coerce_final_answer`（:291-320）是全链路唯一 dict→str 显式序列化点（columns+rows → Markdown 表；其他 dict → json.dumps）。

## 4. MCP 边界确认（审计项 3）

**确证：MCP 是纯 Integration Adapter（Tool 的第二出口），零内部契约职责，不迁移。**

- 代码在仓库根 `mcp_servers/`（不在 backend 内）：manager（基类+路由+超时）、schema_adapter（`langchain_tool_to_mcp_meta` 只从 args_schema 派生**输入**参数，schema_adapter.py:34-51 不碰输出）、2 server（rag 3 tool / sql 2 tool）。
- 反向依赖干净：mcp_servers 全部 import 里**零 `core/tool_runtime`、零 `skills`**；派生型 tool（search_knowledge/sql_query）执行直调底层服务（pipeline.ask / ask_struct）**根本不执行 langchain tool 函数体**——MCP 与内部链路无共享运行时。
- 内部主链路不经 MCP：skill `tool_fn.invoke`（skills/base.py:391）→ tool_runtime executor，grep mcp 零命中；平台自身不是 MCP client（全仓零 ClientSession）。
- 三出口（REST /api/mcp:8000、标准协议 mcp-service:8091 同镜像独立进程、internal_ai 给 Java）全部收敛同一 `manager.route` 信封 `{ok, tool, server, result|error}`，消费方全是外部/跨系统（外部 Agent、Java business-service）。
- **裁决**：MCP 的 server 级 dict 与 manager 信封是**对外**契约，与内部 Tool Contract 分属两个边界，互不污染。禁止把 manager 信封迁移/统一进内部封套。

## 5. 设计裁决与 STOP G 范围

### 5.1 需要迁移/新增（IMPLEMENT 范围，预估小改动）

| # | 动作 | 落点 | 说明 |
|---|---|---|---|
| M1 | **两型契约显式化**：为全部 12 Skill / 17 capability 补齐 output_type 声明（现状仅 4 个显式），消灭「隐式 text」 | 各 skill ClassVar（声明机制既有，skills/base.py:145-146） | 纯声明补齐，不改任何 Tool 函数体 |
| M2 | **解包器收敛**：把 skill_adapter.py:100-107 的手写解包收敛为 `shared/tool_envelope.py` 的单一函数（如 `unwrap_envelope(raw) -> data`，成功返回 data、failed 抛/返标记由调用方定），`parse_tool_envelope` 一并归入 | shared/tool_envelope.py + skill_adapter.py | 唯一生产解包点改走统一出口 |
| M3 | **失败语义对齐**：`validate_semantics` 升级为优先读 `status=="failed"`、保留 error 键嗅探作兼容兜底（双判，向后兼容） | skills/validation.py:288-312 | 封套声明的 status 字段首次成为失败判定的一等依据 |
| M4 | **裸 JSON 透出修复**：text 型 skill 边界（或 reporter 单步骤透传前）识别封套形状 JSON → 按 M2 解包取 data 或转可读文本；先修 map 路径实测复验 | 收敛点定在 skill 边界（reporter 保持哑文本原则不破坏） | 唯一一处用户可见缺陷 |
| M5 | **契约守卫测试**：静态断言「每个 @tool 的实际输出形状 ≡ 其 capability 声明的 output_type」（扫描 return 语句风格 vs 声明，G2 式防漂移门）+ M2/M3 行为测试 | tests/skills/ 扩展或新文件 | 成为第五个契约一致性门 |

### 5.2 禁止修改（红线，与 STOP F 同格式的永久清单）

- `core/tool_runtime` 九件套（executor 透传语义冻结；`ToolResult.status` 保持执行态语义，**禁止**把业务 status 塞进去）
- 34 个 Tool 函数体（**一个都不重写**；E8 的 18 个 text 型维持 Markdown——强套封套=prompt 变胖，是已裁决的反模式）
- tool schema（args）/ `tool_registry` 注册机制 / `langchain_tool_to_mcp_meta`
- MCP 全部（三出口信封是对外契约；mcp-service 容器形态不动）
- `step_results`/`StepResult` TypedDict（state.py:48-62）与 f11 失败留痕契约、SSE 帧序、前端 sources 提取
- SQLSkill dataclass 旁路（SQLResult/BusinessInsight 是 step 间协议）、CS `execute_sql_struct` 旁路、travel 专家函数直调——三者本就不是「Tool 消费」，不纳入契约
- 四个域图 reporter（各有类型化交付契约，STOP F 冻结）
- `TraceMiddleware` 接线与 span 形态

### 5.3 挂账项（不阻塞 IMPLEMENT，实施时顺手裁决）

- `sql_query_tool` 失败分支返回封套而成功返回 Markdown（tools/sql.py:147-153）：按两型契约它应整体是 text 型，失败宜改 `[SQL ERROR] ...` 文本风格——**属 1 个工具的卫生修正，不算重写**；实施时与 LLM 消费口径一并确认。
- email 三件套的 agently CLI JSON 形状：登记为 text 型内嵌结构，不强行改造（agently 是外部 CLI 的信封，不是本平台封套）。
- reporter 容器级 `isinstance` 守卫（:43,131）：P2 加固，可并入 M4 或单列。
- `_format_step_outputs` 把原始 error 全文进 prompt（:422）：脱敏口径与全失败路径（:139-148）不一致，P2。

## 6. 最终判定

```ini
STOP_G_READY=true
NEXT_ACTION=IMPLEMENT_STOP_G
SCOPE=M1 声明补齐 + M2 解包收敛 + M3 status 对齐 + M4 透出修复 + M5 守卫测试
TOOL_RUNTIME_TOUCHED=false
TOOL_FUNCTION_REWRITE=false         # 34 个 Tool 函数体零修改（sql_query_tool 失败分支为挂账例外）
MCP_TOUCHED=false
DOMAIN_REPORTER_TOUCHED=false
EXPECTED_DIFF=shared/tool_envelope.py + skills/{base,validation}+4个skill声明 + skill_adapter.py + tests
```

实施验证计划：`tests/skills/`（含 test_base_output_contract）+ `tests/test_tool_envelope.py` + 契约四件套 + `tests/orchestration`（direct/workflow 支线）+ planner 评估（若 params/描述变更）；M4 需附 map.lookup direct 路径实机复验证据。

最后审计：2026-09-29。
