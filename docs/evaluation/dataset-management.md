# 业务评测集与知识库评测管理

## 操作流程与页面

评测中心默认进入“评测集”，按“评测集 → 审核与候选 → 关联与运行 → 评测结果”安排主流程；线上反馈保留独立入口。知识库目录只管理知识资源，并提供带 kb_id 的“关联评测”链接。

- 导入：下载模板、选择 JSON / JSONL / CSV、校验全部题目，展示行号和字段错误，再提交审核。CSV/JSONL 填写清单；JSON 使用文件内清单。列映射和附加 Schema 放在高级选项。
- 审核：以问题、参考答案、原文证据的可读形式展示全部条目；读取全文、填写说明后才能发布。未审核版本不能关联或执行。
- 新建：直接用普通表单填写多道问题、参考答案与原文证据，保存后进入待审核状态；批量导入作为次入口。
- 修订：使用普通表单修改问题、证据、答案和子集，支持新增题目与归档。保存自动建议递增版本号，新版本重新审核；历史实验不改变。版本指纹、标签和差异放在高级选项。
- 关联：通常只选题集、知识库和评测方式。默认保留固定语料；子集、真实权限测试身份和门禁阈值收进高级设置。
- 运行：提交后立即返回 task_id/run_id，后台执行。展示排队、运行、失败和终态；支持查询、取消、失败恢复，刷新页面恢复上次任务。取消在题目边界/当前模型调用结束后生效。
- 结果：先展示通过题数、未通过题目和原因，逐题展开回答与证据；诊断 JSON 按需查看。VALID 只表示运行有效，不表示质量通过。
- 发布门禁：在关联行展开设置，先读取真实配置，再明确启停。默认不自动启用。启用后上传候选在正式提交前评测，失败保留旧版，增加的耗时与模型用量在界面说明。

## 统一文件契约

最多 100 题、5 MB、UTF-8，schema_version=rag-eval/1.0。未知字段拒绝；上传文件不能授予角色、权限或指定任意执行身份。

```json
{
  "manifest": {
    "schema_version": "rag-eval/1.0",
    "dataset_key": "policy_golden",
    "name": "制度评测集",
    "version": "1.0.0",
    "module": "rag"
  },
  "cases": [{
    "id": "P-001",
    "question": "年休假如何申请？",
    "tier": "core",
    "query_type": "single_doc",
    "splits": ["regression"],
    "archived": false,
    "expected": {
      "behavior": "answer",
      "evidence": [{"doc_id": "真实文档ID", "quote": "真实原文引用"}],
      "reference_answer": "人工核验后的参考答案",
      "required_facts": ["必须包含的事实"],
      "assertions": [{"type": "contains", "value": "需要出现的文字"}]
    }
  }]
}
```

- manifest：三段数字版本，module 仅 rag；可选 description/language、case_schema、csv_mapping。Schema 与用例一起进入哈希和版本，不复用未锁定的外部 Schema。
- case：id 唯一；tier=smoke/core/hard/regression；query_type=single_doc/multi_doc/table/procedural/no_evidence/permission_denied；tags、splits、archived 可选。归档只排除新运行，不删除历史条目。
- evidence：doc_id/source_file 至少一个，quote 必填，version_id 可选。必须唯一映射到目标语料文档；答案题要求对应文档和全部引用命中。
- 拒答：behavior=refuse，reason/query_type 对应 no_evidence 或 permission_denied，证据、答案、事实、断言为空。权限拒答题必须选择服务端可解析的测试用户。
- 断言：contains、not_contains、equals；不执行 Python、JavaScript 或上传的表达式。参考答案和 required_facts 在答案模式消费；检索模式不声称完成答案质量验收。
- case_schema：附加 JSON Schema，最大 16 KB / 12 层；禁止引用及正则表达式，避免远端读取和无界执行。标准契约始终先检查，附加 Schema 不能放宽权限字段或基础约束。

JSON 顶层恰为 manifest/cases；JSONL 每个非空行一题，manifest 通过表单传入。

CSV 默认标准表头 id,question,expected,tags,tier,query_type,splits,archived；expected/tags/splits/archived 单元格使用 JSON（普通字符串字段无需 JSON 引号）。允许 csv_mapping，例如 {"id":"题号","question":"问题","expected":"预期JSON"}。映射是标准字段到来源列名；显式未映射的辅助列不导入。重复表头、错误 JSON、字段类型和重复 ID 均拒绝并显示错误位置。

## 版本、权限和持久化

规范化 manifest/cases 的 SHA-256 决定 import_id；同 dataset_key/version 不同内容拒绝，同内容重传幂等。审核、关联、标签和版本来源单独记账，文件经 fsync 后原子发布。标签固定到审核版本，不提供可悄悄移动的 prod 指针；CI 固定 binding_id/import_id。

业务资产使用 RAG_DATA_DIR/eval_dataset_imports，复用 app_data。新接口仅平台 super_admin、default 租户；报告详情也对业务题集保持同样读取边界。现有知识库目录为平台共享，尚不是租户私有目录，不能通过删除租户守卫假装实现多租户隔离。

权限测试只能引用真实 auth.users.id；角色、状态、部门、租户由统一授权模块重新读取，不能上传任意权限集合。执行者失权则任务失败；测试身份变化使评测无效。只将测试主体用于检索授权，不把模型预算归属改为被模拟用户，也不写该用户的对话记忆。

## 异步执行与恢复

复用 TaskService/tasks、QueueRouter、Agent Worker 壳、Admission、Lease/Heartbeat、Retry 和 Pending Recovery；rag_eval 走已有 report 队列，无第二套任务状态表。

API 请求携带 request_id；任务创建和全局幂等账本在同一 PG 事务提交。相同身份、请求标识和参数返回同一 task_id；不同参数冲突。Broker 故障显式失败，已创建的 PENDING 任务保留供统一恢复。逐题进度与终态写入带 execution_id fencing；重复投递由租约仲裁。恢复沿用稳定 run_id 和逐题 checkpoint，不把失败标为成功。进程在外部模型调用中断时，未完成题目仍可能重新调用模型，不承诺外部模型计费恰好一次。

## 固定语料与质量口径

固定快照保留独立向量 collection、索引模型元数据、完整父块/表格正文与文档登记状态。捕获期间短暂锁定同库相关表；三类存储不同库时拒绝。单 collection 超过 100000 向量拒绝。复跑使用独立 BM25、快照全文和快照可见性，不混用当前向量、父块或审核状态；读取时校验内容与向量清单哈希。物理副本缺失时失败，不回退当前索引。

固定的是捕获的索引状态，不等于已经证明原始文件解析无损。无代次旧索引若存在登记 ID/父块映射不一致，可以保留实际物理状态进行诊断；报告明确给出 corpus_capture_warnings，corpus_source_mapping_complete=false，发布门禁必失败。带代次索引的登记向量 ID 缺失时直接拒绝快照；历史来源缺少父块时保留诊断警告并阻止门禁通过，新候选自身缺少父块时拒绝捕获。不得把旧数据问题包装成完整性 100%。删除文档的跨 collection 清理会使相关历史向量快照失效，此后不能承诺继续复跑；文件和报告的备份/保留仍遵循平台数据生命周期管理。保留快照会增加存储量，当前不自动清理被历史绑定引用的副本。

不选固定快照时，仅对当前发布语料做运行前后对账；变化判 INVALID_DATASET，且无法通过需要固定语料的发布门禁。

检索模式消费真实 Embedding、混合检索和重排；答案模式额外调用 eval_gen，检查真实回答的正确性、依据性、必需事实及断言。答案缺失或必需指标缺失不能通过。规则与模型评分都不是人工真值，需用业务题集持续校准。

## 发布门禁、候选与 CI

业务门禁复用 compute_module_release_gate 三态裁决：运行有效、样本达到绑定阈值、通过率达到阈值、固定语料以及来源映射完整才能 PASS。默认最少 5 题、通过率 0.95，可在关联高级设置中配置。INVALID 与质量 FAIL 分开。

上传发布点在 registry CAS 之前读取启用策略，为“当前语料移除旧文档 + 候选文档”构造固定评测语料，执行审核版本；FAIL/INVALID/执行异常均不进入提交点。配置者失去超级管理员权限时也拒绝，需合法管理员重新配置。门禁未启用时保留既有上传行为。

Trace/反馈候选可在表单补齐原文证据和参考答案后，转换成统一业务题集的待审核版本。旧内置 Suite 审核入口仍保留，不将其历史批准状态等同于新题集自动发布。

Ragas 合成复用登记的 `eval_gen` 云模型、统一请求预算和本地 Embedding，调用锁定版本 0.4.3 的 [`TestsetGenerator.generate_with_langchain_docs`](https://docs.ragas.io/en/v0.4.3/references/generate/) 与默认图转换。题型分布通过 Ragas 的 `query_distribution` 指定，详见 [Ragas 0.4.3 题型合成器参考](https://docs.ragas.io/en/v0.4.3/references/synthesizers/)。生成器只开放 Ragas 原生的单跳具体、多跳抽象、多跳具体三类；管理端预设 5 题为 3/1/1、10 题为 5/3/2、20 题为 10/5/5，也可调整各类题数。多知识库仍分别形成数据集；完整数据集题型计划会随拆出的每个任务提交，单任务仍为 1–5 题。

业务题集导入与 RAG 静态用例校验共用题型契约，允许单跳/多跳、表格、流程、无答案、权限、歧义、版本冲突、对抗等已定义类型；多跳导入要求两段不同证据。Ragas 混合生成只生成它原生支持的三类题，其余类型需从已核验业务资料人工编写或接入专用生成策略，不会把单跳结果改标签冒充成这些类型。

生成源限于冻结快照中当前可见的文档，按原始 `doc_id` 汇合分块，最多取 12 个来源文档；不足 Ragas 默认转换长度要求或图中没有可生成的场景时明确失败，不降级为单跳。结果按 Ragas 返回的 synthesizer 名称核对题型配额；多跳证据去除 `<1-hop>` 等生成标记后，仍须有两段不同、可唯一映射回原文的证据。答案中的数值须有证据支持。题型、快照、来源任务与完整分布保留在待核验候选及 lineage 中；人工批准并发布前不会成为正式数据集。模型预算仍由统一服务约束，单批运行只在生成前后检查任务取消/权限，因此长时间 Ragas 建图期间的取消可能要等当前批次结束才生效。分批会重复建图，题量较大时成本会高于直接调用单跳合成器。

Ragas 生成候选批准前必须由人工完成五项质量核验：问题清晰且不重复、答案事实有原文支持、必需事实完整、证据覆盖答案、题型符合实际推理步骤。审核理由至少 20 字并与五项结论一起写入审核注记；回答题需要补齐必需事实，多跳题还必须有两条不同的已验证证据锚点。未完成这些检查的候选不能批准或进入 DatasetVersion。

候选列表可通过 `dataset_key` 参数在服务端限定数据集范围；审核历史通过 `GET /api/evaluation/v2/candidates/{candidate_id}/reviews` 查询。接口要求 admin 及以上角色，并按当前租户与候选 ID 过滤；返回审核人、结论、候选修订、理由、当次证据、质量核验项和审核时间。审核记录为追加式，不随候选状态刷新而丢失。

任务管理器将 FAILED 且错误可重试、预算未耗尽的任务展示为 RETRYING。用户取消此类等待重试任务时执行带重试条件的数据库 CAS，成功后终止重投；重试已耗尽或错误不可重试的 FAILED 仍为终态，不允许被改写。

已有 CLI 可执行固定业务绑定：

```text
python -m backend.evaluation rag --dataset managed:<binding_id> --no-ragas --strict-gate --triggered-by <CI操作者>
```

使用与服务相同的资产卷和保留索引；PASS=0、质量失败=1、运行无效=3。内置 Suite 继续使用原有门禁，不把小业务题集套成内置大基准。

## API 摘要

全部新接口位于 /api/evaluation：dataset-imports（上传、validate、detail、review、revisions、diff、labels）；evaluation-corpus/{kb_id}/snapshots；evaluation-bindings（列表、创建、run）；evaluation-tasks/{id}（状态、cancel、resume）；evaluation-publication-policies/{kb_id}（读、启停）；dataset-candidate-promotions；dataset-synthesis。

## 验收边界

工程定向测试、真实模型检索、真实 Broker/Worker 和登录后的浏览器操作分别记录，不互相替代。当前平台仍限单轮 RAG、共享 default 租户；不声称已完成多轮 Agent 评测或租户私有知识库架构。

### 运行目录

API 与全部代码 Worker 共享同一个 eval_runs 挂载，保证后台任务可写、结果页可读。仅共享 app_data 而 API 单独绑定 eval_runs 会造成目录权限或报告不可见问题；本机覆盖层需要保持同一宿主路径。
