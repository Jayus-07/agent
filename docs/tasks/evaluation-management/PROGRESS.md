# 进度

- 基线：main c9bfa8c；新建独立工作树 evaluation-management，初始 Diff 为空。
- 已核实：当前目录和审核依赖文件存储，app_data 已挂载；RAG 内置 runner 固定 rag_eval_kb；离线 API 未消费所选 Suite。
- 已实现：JSON/JSONL 严格导入、行字段错误、全文读取与版本导出；哈希身份、版本冲突保护、原子持久化；服务端超管/default 租户守卫；不可变审核、知识库绑定与真实检索执行；运行前后语料快照变化判 INVALID_DATASET。
- 页面：业务题集/内置基准按需切换；独立“关联与运行”“候选审核”；知识库目录显示关联数量并携带 kb_id 深链接；评测结果端选择实际内置 Suite，离线 API 修复未消费 Suite 的问题。
- 审查证据：本地 Diff 核对上传体限制（含 multipart 解析前）、路径 ID 白名单、未知权限字段拒绝、服务端审核者、不可变版本/内容哈希、固定绑定、语料漂移及失败不报成功。未进行云端 Review，无 PR；未对全仓进行重复审计。

## 实际验证

- T0：py_compile（新增导入/API、service、RAG runner）及 git diff --check 通过；管理端标准 tsc --noEmit 通过。
- T1/T2 后端：主仓库缓存 venv 执行 test_evaluation_imports、test_evaluation_dataset_governance、test_managed_dataset_execution、test_rag_eval_runner_scope、test_eval_provenance、test_json_safe_and_validity_ledger；33 passed，无 skip，约 12 秒。覆盖 admin 拒绝、非 default 租户拒绝、超大请求、错误行、未知权限字段、版本冲突/篡改、证据错配、真实选中 Suite、执行 scope 与语料漂移。
- T1/T2 前端：ImportedDatasetsPanel、EvaluationBindingsPanel、DatasetGovernancePanel、EvaluationCenter、EvaluationResultsPanel、KnowledgeWorkbench、KbAuthorityPanel；7 文件 16 passed，无 skip。覆盖校验失败禁止提交、全文读取后审核、知识库深链接、关联失败可见、INVALID 不报质量通过等。
- 真实运行：D:/tmp/evaluation-management-live.py 在最新 app 容器内，通过 TestClient 注入验收身份，其余知识库目录/文档映射/Embedding/重排/检索与 EvaluationService 使用真实实现。资产和报告写隔离临时目录，评测台账写入替换为空操作，避免污染共享历史。policy_general 的 28 篇发布文档内，单条原文证据冒烟 status=pass、validity=VALID，记录题集 hash 与语料快照；断言没有生成回答和 sem_* 影子评分。此项仅证明链路可用，不代表真实问题集的总体检索质量。
- 部署：在本任务工作树执行 devctl.bat rebuild /y（agent 项目，基础 compose + 本机覆盖层）；保留主仓库既有 eval_runs 挂载。最终返回 0，app/rag-service healthy，8000 health=200；3200 登录、知识库页、评测页 HTTP=200。原有 app_data 中的探针跨容器重建读取成功，验证后已移除探针。
- 启动问题已处理：Redis 恢复 AOF 时健康检查短暂失败，恢复完成后重建成功；Windows 跨盘 node_modules junction 使 Next 编译失败，改为复制已有依赖缓存，无重新下载。旧依赖链接保留在被忽略的 node_modules/.shared-main 内，未删除或修改主仓库依赖。前端从本任务工作树在 3200 运行。

## 范围与未验收项

- 当前用户浏览器处于登录页；未执行登录后的浏览器点击全流程，不能把 HTTP 编译、组件交互和带身份依赖覆盖的 API 验证说成完整浏览器验收。
- T3 全量/批量质量评测未运行；无需默认全仓回归。未验收多租户、候选索引与模拟身份，这些功能没有开放。
- 当前发布语料通过前后快照核验，不是事务快照隔离，不能承诺任意历史物理索引可重建。
- 与通用平台的差距：逐条版本编辑/差异、CSV 字段映射、版本标签/可编辑子集、自定义 Schema、通用断言与上传资产 CI、Ragas 合成、可靠异步队列/任务幂等仍未实现，现行说明见 docs/evaluation/dataset-management.md。
- 状态：工程与单题真实链路 PASS；登录后浏览器全流程 UNVERIFIED。任务分支 codex/evaluation-management，基线 HEAD c9bfa8c，修改未提交；按项目规则等待用户明确提交确认，不合并 main。

## 第二阶段（用户已授权提交合并，进行中）

- 新增：CSV 标准列/映射及 Schema；逐条表单修订、新增、归档、固定标签和差异；子集；答案评测及确定性断言；真实用户身份引用与动态授权核验；固定向量/全文/BM25/生命周期快照；发布 CAS 前的候选语料门禁；Trace/反馈转换为统一待审核题集；Ragas 异步候选合成；既有 managed: CLI 与三态门禁接入。
- 异步：复用 tasks、TaskService、QueueRouter(report)、Worker 壳、Lease、Admission、Retry/Recovery；PG 同事务幂等创建任务；稳定 run_id、逐题 checkpoint、进度、取消、失败恢复和报告留痕。执行服务已从 HTTP 路由抽到 managed_execution，供 Worker/发布调用。
- 界面按最新要求简化：主流程顺序为评测集、审核与候选、关联与运行、评测结果；普通表单编辑问题/答案/证据，高级设置默认收起，权限身份通过账户搜索选择，发布门禁先读取真实状态再启停。结果先展示通过题数和原因；取消明确说明边界生效。
- T1/T2（第二阶段第一次）：后端 113 passed，无 skip，约 48 秒；前端 9 文件 18 passed，约 5 秒；标准 tsc 与 py_compile/diff check 通过。此后还有小范围来源映射、授权读报告与生成错误口径修改，需最终复跑对应集合。
- 真实固定语料检索：D:/tmp/evaluation-enterprise-live.py 使用真实 policy_general（28 文档、471 向量）和真实 Embedding/重排，单题 pass、VALID，物理快照启用。该次发生在新增严格登记映射核验前，不可据此宣称原始文件完整性通过。
- 新核验发现历史种子 p4_kb_seed_doc.md：登记 82 个向量 ID，实际 40 个向量，旧全文记录缺少可靠 Chunk ID/粒度，且没有索引代次。保留实际索引可用于诊断，但记录 capture_warnings/source_mapping=false，发布门禁拒绝；不修改共享种子数据、不将其宣称修复或完整。带代次索引缺失则直接拒绝固定快照。
- D:/tmp/evaluation-enterprise-worker.py 将验证真实 Broker/Worker/任务幂等和终态；第一次在严格映射处正确拒绝，尚未完成队列验收。需要用最新镜像继续。
- 两次 devctl.bat rebuild /y 已成功复用依赖缓存；有后续代码修订，因此仍需最终重建。3200 仍运行任务工作树前端，8000 主栈后端；不触碰 ragu1/CI/profile 栈。
- 用户选择“先继续改造”，未登录；登录后的浏览器全流程仍 UNVERIFIED，不重复索取凭据。
- 当前未提交；main 仍 c9bfa8c 且干净。最终待办：最终定向验证与本地 Diff 审查 → 最新源码重建及真实 Worker/答案/Ragas 可行验证 → 记录剩余外部限制 → 提交并合并 main（授权已给）。

## 最终验收推进

- 最新定向验证：后端 125 passed（无 skip）；前端原 9 文件 18 passed，加新建题集表单 1 passed，共 19；标准 tsc 通过；新增核心 Python 文件 ruff check 通过，git diff --check 通过。
- 新建入口改为普通多题表单；批量上传为次入口，表单错误就地展示；拒答题可选择无证据或无权限原因。轮询避免重复请求和迟到更新。
- 真实 Worker 暴露 API/Worker eval_runs 挂载不一致导致 PermissionError，已在 compose 统一；重建后以 Worker 写探针、API 读取同一探针实际通过，并移除探针。
- 真实 Worker 继续发现本机 report-worker 224m 内存上限导致 SIGKILL/OOM；调整本机/4C8G 覆盖层为 768m，生产预算同步。OOM 任务 fcc049ab-4b3d-4cdc-a333-b76a1fe9922a 已请求取消，输入保留等待既有租约恢复机制收敛，未直接改写任务状态或删除正在引用的资产。
- 真实 Ragas 任务 517960b3-a54c-4617-bf3a-9d027bb56be1 触发兼容异常：镜像 langchain-community 0.4.2 删除了 Ragas 0.4.3 仍导入的 VertexAI 模块。主机缓存 venv 的 0.4.1 可实际 import ragas.testset；约束 community<0.4.2，正在用既有 BuildKit pip 缓存重建。此时不得报告合成成功。
- Worker 提示词快照原来缺少 run_on_worker_loop 导入，已补充并通过单测；最新日志确认真实 DB prompt snapshot 加载成功。
- OOM 探针任务随后由既有恢复机制完成，真实 TaskService 状态 SUCCESS、报告 VALID、固定语料启用，单题质量 fail、gate FAIL（未伪造通过）。确认终态后仅清理本脚本创建的题集、绑定与独立向量副本，任务/报告审计保留。
- 新增本机/生产报告 Worker 资源配置与 Ragas 依赖约束回归：24 passed（包含前次 23 项，新增 1 项）。新增测试最初错误依赖 Python 3.11 的 tomllib，已改为兼容项目 Python 3.10 的文本约束核验，重跑通过；无 skip/xfail。
- T0 最终：全部改动 Python 文件 py_compile 通过；核心新增 Python 文件 ruff check 通过；标准 tsc 通过；暂存 Diff --check 通过。匿名真实接口返回 401，health=200、3200 页面 HTTP=200；后者仍不替代登录后浏览器验收。

- 真实重试暴露展示契约问题：既有状态机使用 FAILED 表达等待自动重试。新增 task_view 复用既有错误分类/行级预算/耗尽标记，返回 RETRYING 与 raw_status，避免前端或验收脚本提前认定终态；不改变原任务状态机。新增边界测试，相关后端 35 passed；相关前端 2 文件 3 passed，标准 tsc 通过。
- 新建表单保存成功后只显示审核链接和“继续新建”，重置名称/题目/资产标识，避免编辑已保存表单复用旧版本冲突；实际 React 交互回归通过。
- 最终整体定向验证：后端同一集合 127 passed、0 skip，56.69 秒；前端 11 文件 20 passed，7.08 秒；标准 tsc 通过。新增合成面板交互用例覆盖 RETRYING 后继续轮询到 SUCCESS。
- 最新源码重建成功（devctl.bat rebuild /y）；依赖修复后容器实际 import ragas.testset 通过，langchain-community=0.4.1、ragas=0.4.3。report-worker 实际上限 768MiB。
- 真实异步检索：585cebb2-9313-42b3-bde7-ea68890ab37b，真实 PG 幂等、Broker、Worker、Embedding/重排、固定语料、报告，SUCCESS/VALID；相同 request_id 返回同一 task_id。
- 真实答案评测：3900f723-46ec-407d-9c48-7a23bc4250de，SUCCESS/VALID；真实配置模型响应、非空 generated_answer、gen_S6_answer_correctness 指标均断言通过。两个任务 gate=FAIL，不把执行成功等同质量通过。Worker 实测约 571MiB，未再 OOM。该项仅验证单题功能链，不能证明业务质量达标。
- 两项探针终态后只清理本脚本创建的输入资产与独立向量副本，任务/报告台账保留；主知识库原文与正式索引未改动。Ragas 真实候选合成正在单独验收。
- Ragas 最初完整图转换虽然已能调用真实模型，但 20 段摘要超过平台每请求 8 次调用的预算，属于新功能与既有预算的适配缺陷。没有提高/关闭平台预算；已删掉重复的全图摘要/主题/角色生成，改为从可见固定内容块直接调用 Ragas 单跳合成器，每批 1–5 题、每题 4000 字、每题边界校验取消和权限，保留默认预算和重试余量。新增定向测试实际构建 Ragas Scenario，验证隐藏/重复来源排除、调用次数、预算身份及超限题数拒绝，1 passed。
- 新合成任务使用独立 Trace、稳定 task_id 预算标识；Worker 入口清除前一任务的 Trace/日志绑定，避免模型用量串到上一个评测用例。异常 Span 明确记 error，不伪造合成成功。
- rapidfuzz 已补进 ragas optional dependencies，版本范围 >=3,<4；容器临时安装只用于定位缺项，不作为最终部署证据。最终仍须由最新源码 devctl rebuild 生效。预构建只 build、不 up，不打断运行任务。
- 旧预算验收任务 5bb55427-4b2c-43de-9417-7705c72cd289 已先通过取消机制置标志，再仅终止其确切 Celery 子任务 92fa5090-07bc-4e39-9063-891175adc822（单个验收任务，不停止整个服务）；保留输入，等待统一恢复机制收敛。旧脚本超时明确失败，未记为通过。

## 收尾复核（2026-10-23）

- 复核等待自动重试的取消语义：FAILED 仅在可重试错误、`retry_exhausted=false`、计数仍有余量时视为 RETRYING；数据库 CAS 同时校验这些字段。CAS 竞态若任务仍等待重试则下发取消标志；若 Worker 已认领则走运行态边界取消；永久失败与耗尽失败保持 FAILED。更新了状态契约文档。
- 复核批量答案评测预算：托管题集按 `run_id + case_id` 绑定稳定请求预算 ID；用户 ID 归一为统一预算服务使用的主体 ID，租户额度仍走现有用户/租户总额度守卫。内置 Suite 保持现行范围。静态检查先发现 runner 忘记从 kwargs 固定读取 `run_id`，已修复并复验。
- 新增无数据库副作用的取消/CAS/SQL 参数和逐题预算单测。最终定向集合：138 passed、4 deselected、0 skip/xfail。测试环境将 `PGDATABASE` 与 `MEMORY_PGDATABASE` 指向不存在的隔离库；早期一次未排除的 App 搜索集成测试因此无法连共享库并返回授权拒绝，随后明确排除相关搜索用例重跑，其余覆盖通过。该现象是环境隔离结果，不计入代码通过。
- T0：任务工作树全部改动 Python 文件 `py_compile` 通过；任务控制相关 Ruff 和 RAG/service 的 F821 检查通过；`git diff --check` 与暂存 Diff 检查通过。全文件 Ruff 另报 12 条导入排序/未使用导入问题；将两份文件与基线 `c9bfa8c` 的原文件分别扫描，基线同样报告这 12 条，本轮未扩大范围处理。
- 最新工作树 `devctl.bat rebuild /y` 完成；app、agent-worker、rag-index-worker、report-worker、maintenance-worker 等服务均健康，8000 `/health`=200，3200 评测页 HTTP=200。前端本轮没有新增修改；前端 11 文件 20 passed、tsc 通过的证据来自前一轮最终复验。
- 真实 TaskService/Redis 验证：等待重试探针 `ba3ecdd4-3b4f-4e09-822f-0e30fb9a71cf` 首次取消落为 CANCELLED，重复取消幂等；已耗尽探针 `97b2ca73-1c69-4ab6-98ae-4f4c4c0d671a` 保持 FAILED。
- 真实 report Worker 三题答案评测 `6033d5ac-dcf5-490e-ad5b-563eba18533d`：SUCCESS/VALID，3/3 题有非空生成答案和 S6 指标，每题使用不同预算 ID；三题质量状态均为 fail，因此只证明多题预算下执行完整，不代表答案质量通过。终态后只清理本次创建的题集、绑定和独立向量快照，任务/报告记录保留。
- 旧共享库测试中止后残留的本轮测试任务 `df6120ea-0f75-4b4d-993f-05bc15a71a9c` 在复核时已自行从 RUNNING 收敛为 FAILED，Worker active 为空；保留审计记录，没有按前缀清理其他测试任务。
- 剩余边界：登录后浏览器操作 UNVERIFIED；候选文档上传触发发布门禁的完整 T3 未验收；历史 `policy_general` 来源映射缺失仍会使发布门禁 FAIL；已知 `test_case_h_metadata_shadow_route` 在干净 main 也失败，本轮不恢复废弃路由、不屏蔽失败。当前 main 在合并前复核仍为干净的 `c9bfa8c7adf22cd708dab52caebbeb539e7891cf`。
- 状态：实现、定向验证、提交和首次非快进合并均已完成。功能提交 `0c7abe41122b1c587bf68f57ff629b5436f32d36`；main 合并提交 `31ce692185657b6d62a58ac1b09f12b4146254a7`。任务已完成；保留工作树供 3200 的本地前端会话继续使用。
