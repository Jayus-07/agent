# 评测数据集盘点（2026-09-15 整理）

> 位置：`backend/evaluation/datasets/`。加载入口统一为 `backend/evaluation/dataset/loader.py::load_dataset(module, selection)`。
> 优先级：`selection`（suites/）→ `{module}/cases.jsonl`（V3 canonical）→ `{module}/` 目录分片 → 顶层 `{module}_v2.json` / `{module}_test_kb.json` / `{module}.json`。文件名含 `.deprecated.` 一律跳过。

## 各模块现状

| 模块 | Canonical | 条数 | 版本 | 加载方式 | 备注 |
|------|-----------|------|------|----------|------|
| rag | `rag/cases.jsonl` | 100 | v4.0 | `load_dataset('rag')` | 唯一有 suites 的模块：`smoke`(19) / `quick_26`(26) / `ci_golden`(99) / `feasibility_10`(10) |
| planner | `planner/cases.jsonl` | 20 | v1.0 | `load_dataset('planner')` | 2026-09-15 由顶层 planner.json 迁入 V3，内容逐条一致 |
| cs | `cs/cases.jsonl` | 20 | v1.0 | `load_dataset('cs')` | ID 体系 CS-001~020；manifest 2026-09-15 补齐 |
| sql | `sql/cases.jsonl` | 15 | v1.0 | `load_dataset('sql')` | 含 S014~S017 安全用例 |
| e2e | `e2e/cases.jsonl` | 25 | v1.0 | `load_dataset('e2e')` | manifest case_count 2026-09-15 由 13 校正为 25 |
| planner_params | `planner_params.json` | 8 | v1.0 | `backend/tests/evaluation/test_planner_params.py` 直读 | live 模式（真实 LLM）参数填充集，路径被测试硬引用，**勿移动** |
| tool_selector | `tool_selector_eval.json` | 67 | v1.3 | `backend/evaluation/run_tool_selector_eval.py` | FC 工具选择独立跑批，不走统一 loader |

## 特殊文件

- `rag_test_kb.json`（v4.0，103 条）：**双重角色**——既是 rag 评测知识库的注册源（`backend/config/knowledge_base.py` 的 kb_id `rag_test_kb`），也被 rag 检索/indexer 测试引用。活跃，勿动。
- `rag.v1.deprecated.json` / `rag_v2.deprecated.json`：历史存档，loader 自动跳过。
- `cs.v1.deprecated.json`：旧 cs 集（ID CS001~020），与新集零交集，2026-09-15 标记弃用。
- `planner.v1.deprecated.json`：迁移前的顶层 planner.json 原件留档。
- `GROUND_TRUTH_ENRICHMENT_PLAN.md`：数据集增强计划文档（历史）。

## 维护约定

1. 新增/修改用例一律改 `{module}/cases.jsonl`；rag 模块改完记得同步受影响的 suites（suite 只存 case_id 引用，不会自动适配期望变化）。
2. manifest 的 `case_count` 必须与 cases.jsonl 行数一致（本次发现 e2e 曾失同步）。
3. 弃用集不删除，改名加 `.deprecated.` 中缀 + `_DEPRECATED`/`_replacement` 字段，loader 与 storage 均按此约定拦截。

## 模块 ↔ 测试知识库对照（2026-10-08 修正版，manifest kb_id 为权威）

> 口径：**独立测试库 = audience=test 的隔离库**（合成/受控数据，禁止对客，UI 按设计不可见、
> 只能内部脚本/fixture 灌数）。cs_faq/travel/policy_general 均为**生产对客检索库**，
> 不得声明为测试库，不得灌测试性质语料。测试库（audience=test）仅 super_admin 主体可见/可用（authz include_test 口径，2026-10-08 拍板），UI 上传/检索治理用，对客链路恒不可见。

| 模块 | kb_id | 性质 | 说明 |
|------|-------|------|------|
| rag | `rag_eval_kb` | ✅ 唯一独立测试库（audience=test） | 269 条全绑定；语料源=data/docs/{rag_test_kb,rag_100_docs,rag_eval_kb}（兼容目录，勿删）；UI 不可见，走 fixture/脚本灌数 |
| cs | `cs_eval_kb` | ✅ 独立测试库（audience=test，2026-10-08 建） | 评测本体零检索依赖；合成测试语料存此库；域图 live 仍走生产库 cs_*，接线为后续任务 |
| travel | `travel_eval_kb` | ✅ 独立测试库（audience=test，2026-10-08 建） | 离线评测零检索依赖；合成测试语料存此库；live 探针仍走生产库 travel，接线为后续任务 |
| sql | （空） | 非 RAG | NL2SQL 直连业务库 agent_business |
| planner | （空） | 无检索依赖 | 只评任务拆解 |
| e2e | （空） | 无检索依赖 | Graph 故障注入 |

`content_hash` 只锁 cases.jsonl，manifest 加字段不触发不可变锁。
