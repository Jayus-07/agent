# TASK: 评测中心简化改造（前后端）

## 目标

把管理端评测中心从 8 tab 简化为 3 个核心 tab（数据集 / 运行评测 / 结果对比），
把深入功能做进核心链路；后端以 evaluation_v2 的 datasets/runs/results 为主干收敛；
提示词治理与评测中心的联动收口；运行快照补齐被测模型等指纹。

原型已评审定稿：`冻结区 = 阶段二/三（候选池自动化、AI 起草、门禁自动化），本次不实施`。

## 范围（用户已确认的边界）

### 本次做

1. **前端**：`frontend-admin` EvaluationCenter 8 tab → 3 tab：
   - 数据集：列表（版本号→版本历史：对比/导出/派生草稿/回退/发布/弃用语义）、
     导入向导（模板下载/上传/校验预览/跳过错误行）、从知识库生成（快照选择/生成预览/
     勾选采纳/数字校验徽标）、草稿编辑器（与导入模板同 schema：问题/参考答案/必需事实/
     证据引用 + 行级校验）、证据选择器（快照内切片搜索/自动检索/回填）、从 Trace 添加入口
   - 运行评测：发起表单（数据集/评测方式四模式/被测配置/知识库快照）、
     运行列表（来源标记/评测方式/状态/进度/取消重试）、运行环境指纹展示
   - 结果对比：指标分组（自研/RAGAS/成本时延）、口径校验、逐 case 详情
     （检索命中与判分裁决表/Trace 跳转 `?tab=traces&q={trace_id}`）
2. **后端**：evaluation_v2 主干保留 datasets/runs/results；V1 端点仅保留仍被
   Prompt 页 / 首页 / Trace AddToEval / KbAuthorityPanel 依赖的四个。
3. **新能力（小）**：
   - `GET /evaluation/v2/import-template?module=&format=jsonl|csv|fields`（模板三件套）
   - `POST /evaluation/v2/datasets/evidence-search`（快照内证据切片检索，关键词口径 MVP）
   - 运行快照补齐：eval_gen 生成模型指纹进 `runtime_environment.models["generation"]`；
     断言改子集口径（历史快照兼容）；非 RAG environment 补 prompt_versions；
     `GET /runs/{id}` 返回 compact `fingerprint` 供前端展示
4. **联动收口（提示词治理 ↔ 评测中心）**：
   - Prompt 页「运行评测」按钮改为深链跳转 `/evaluations/center?tab=runs&dataset=…&prompt=…&pv=…`
   - 被测配置下拉以 Prompt 版本 API 为数据源
   - 运行列表标「来源」（发布评测 / 手动）

### 冻结区（本次不做）

候选池与按域自动收录、AI 归因/AI 起草真值、批量确认视图、门禁自动化、
多跳/对抗题生成、人工评分、结果导出、Suite/Experiment/Gate UI。

## 验收

- 前端 3 tab 全部交互可用，砍掉的 tab/组件/端点不再被引用（tsc 通过、组件测试绿）
- 后端新增端点带契约测试；`test_runtime_environment` 覆盖子集断言与 generation 指纹
- `devctl.bat rebuild` 后健康检查通过；原型对应的每个交互在真实页面可用
- 文档同步：`docs/evaluation/evaluation-governance.md` 等受影响文档按需更新

## 关键代码事实（设计依据）

- 评测方式唯一口径：`backend/evaluation/mode.py`（offline/semantic/ragas/self+ragas）
- 生成器：`backend/evaluation/dataset/synthetic.py`（SingleHop 合成器 + 快照选片 +
  数字一致性校验 + 与导入同一 validate_upload）
- 转正事务：`evaluation_v2/repository.py review_candidate`（八道校验 + case_revisions 不可变修订）
- 导入格式：`.jsonl/.json/.csv` 均已支持（`evaluation_v2/imports.py`，5MB，csv_mapping）
- 检索命中明细：rag runner details（doc_id/title/chunk_id/rerank_score/snippet +
  permission_denied/version_visible 裁决语义）
- case 结果已落库 trace_id/latency/cost（eval_case_results）
- Trace 深链已验证可用：observability 关键词搜索匹配 record.id 子串
- 运行快照已有：prompt_versions/dataset_version+hash/corpus_snapshot+hash/
  evaluator_versions/runtime_environment(embedding/rerank)；缺 generation 指纹
