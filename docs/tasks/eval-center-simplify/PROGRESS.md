# PROGRESS: 评测中心简化改造

- Worktree: `D:/Program Files/workplace/agent-eval-simplify`（分支 `task/eval-center-simplify`，基线 870e829）
- 状态: **IN_PROGRESS**

## 已完成

### Phase B/C 前端（2026-10-25）
- [x] `EvaluationCenter.tsx` 重写：8 tab → 3 tab（数据集/运行评测/结果对比），
      旧 tab 名（overview/generate/review/suites/results/bindings/gates/feedback）统一别名收敛，
      Prompt 深链 `?tab=runs&prompt=<key>&pv=<n>` 预填
- [x] `DatasetGovernancePanel` 重写：V2 数据集面板 + 折叠「候选审核与修订」
      （EvaluationV2ReviewPanel 并入，闭环不缺）+ 折叠「从知识库生成」；
      移除 business/builtin 旧 V1 切换
- [x] `EvaluationV2DatasetPanel`：导入区新增模板下载三件套
      （`ImportTemplateButtons` → 新端点）；文案与跳转适配三 tab 结构
- [x] `EvaluationV2RunsPanel`：新增运行环境指纹卡（来源标记/生成模型/
      embedding/rerank/数据集版本/语料快照/Prompt 版本）；候选 Prompt 版本
      改为「Prompt 选择器 + 版本号」（保留高级 JSON 折叠）；深链预填
- [x] `api/evaluation.ts`：getV2Run 返回 {run, fingerprint}；新增
      downloadImportTemplate / searchDatasetEvidence / EvaluationV2RunFingerprint
- [x] Prompt 页（Phase C）：V1 直接评测移除，「运行评测」拆为
      「发起发布评测」（release 门禁，保留）+「到评测中心运行」（深链）
- [x] 删除无引用的 V1 兼容面板与旧页面组件（30 个文件，均经 grep 确认零引用）：
      概览/方案/门禁/审核/V1 结果/绑定运行/候选转移/导入旧面板/修订编辑器/
      components/evaluations 旧目录/TrendCharts 及其测试
- [x] 后端 run.source 字段（manual 默认）进快照与指纹，UI 来源标记就绪

### Phase B/C 验证
- [x] `tsc --noEmit` 全仓通过
- [x] vitest 评测+观测目录：**48/48 通过**
- [x] vitest 全仓：543/547 通过；4 个失败（rbac.test 1 + RoleGate 3）经主仓
      对照确认为存量问题，与本次改动无关

## 已完成

### Phase A-1 后端：模板下载端点（2026-10-25）
- [x] `backend/evaluation_v2/import_templates.py`：模板单一事实源（RAG JSONL 样例 /
      非 RAG CSV 样例（UTF-8 BOM）/ 字段说明 markdown），字段口径与 imports 校验器一致
- [x] `GET /evaluation/v2/import-template?module=&format=jsonl|csv|fields` 路由
- [x] 契约测试 `backend/tests/evaluation_v2/test_import_templates.py`

### Phase A-2 后端：证据切片检索（2026-10-25）
- [x] `backend/evaluation_v2/evidence_search.py`：快照 fulltext 关键词评分
      （中文 2-gram + 拉丁词，无模型依赖，MVP 口径）
- [x] `POST /evaluation/v2/datasets/evidence-search` 路由（admin 门禁，快照加载）
- [x] 纯函数测试 `backend/tests/evaluation_v2/test_evidence_search.py`

### Phase A-3 后端：运行快照补齐（2026-10-25）
- [x] `backend/evaluation/generation.py` 新增 `peek_eval_model()`（解析 eval_gen
      模型名+供应商，不做凭据可用性探测）
- [x] `runtime_environment.current_runtime_environment()` 增加
      `models["generation"]` 指纹
- [x] `assert_runtime_environment` 改子集口径：历史快照（无 generation 键）重试
      不被部署误伤；快照中存在的角色键逐一比对
- [x] 非 RAG 运行 environment 补 `prompt_versions`
- [x] `GET /evaluation/v2/runs/{run_id}` 返回 compact `fingerprint`
      （runner/module/dataset_version_id/prompt_versions/evaluator_versions/
      generation_model/embedding_model/rerank_model/corpus_snapshot_id）
- [x] `test_runtime_environment.py` 扩展：generation 指纹、子集断言兼容

## 已验证（Phase A 实测）

- `pytest backend/tests/evaluation_v2 -q` → **123 passed**（含新增 9 个：
  generation 指纹、子集断言兼容×3、模板契约×4、证据检索×3）
- `pytest backend/tests/evaluation -q -k "generation or evaluator or mode or synthetic"`
  → 25 passed, 4 skipped（旧体系无回归）
- 全部改动文件 `py_compile` 通过
- Trace 深链 `?tab=traces&q={trace_id}`：observability 关键词搜索匹配 record.id
  子串，代码级确认可用（rebuild 后待运行时复核）

## 待办

- [ ] 证据选择器 UI 接线：`searchDatasetEvidence` 端点与前端方法已就绪，
      待接入 EvaluationV2ReviewPanel 的证据声明区（快照内搜索 → 勾选 → 回填
      doc_id/quote）
- [ ] 版本历史视图（原型中的时间线 + 派生草稿/回退按钮）：依赖后端新增
      「以旧版本内容派生新草稿」端点（copy-on-write），本次未做；现有面板
      已有版本清单/diff/覆盖率
- [ ] **Phase D 验证（下一会话/确认后执行）**：
  1. `docker ps` 核查共享栈归属 → `devctl.bat rebuild`（用本工作树代码重建）
  2. 健康检查（:8000 /health、:3200 页面可达）
  3. 原型逐交互复核：模板下载、导入预检、生成、候选审核、运行发起（四模式）、
     指纹卡、对比、Trace 深链、Prompt 页深链
  4. 运行时验证 `?tab=traces&q={trace_id}` 实际命中
- [ ] 文档同步：evaluation-governance.md（评测中心入口与三 tab 结构）、
  frontend-guide.md（如涉及）；tool 版本指纹（tool registry 版本机制待查，本次未做）
- [ ] 合并 main 后清理 worktree 与 node_modules 联接（junction 随分支删除即可）

## 阻塞 / 风险

- V1 端点删除范围需在 Phase B 完成后按「前端实际引用」二次核对
  （prompts 页 runEval、首页 listRuns、AddToEvalModal from-trace、
  KbAuthorityPanel publication policies 四处已知外部依赖）
- `resolve_provider(strict=True)` 在 eval_gen 未登记时抛错 → peek 已兜底返回空，
  快照中 model 为空即"未配置"，运行仍会走 resolve_eval_model 的原有失败路径
