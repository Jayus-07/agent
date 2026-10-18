# 本机诊断与修复证据（2026-10-18）

状态：**真实Smoke失败，RAG真实验收UNVERIFIED，MERGE_READY=false**。后续不得重跑付费流水线。本记录替代早期“缺少模型配置”的判断：数据库原本已有完整配置，阻塞来自先前未核实正确配置路径。

## 版本与审查

- 冻结main：`b4341381a022839108029f862805b02e1a980fb6`；真实评测代码：PR #52 HEAD `440a08e3bf65433fd125f7f10c2fac8c33ecbf0d`。容器内使用缓存的真实Git二进制回读HEAD，不伪造版本信息。
- GitHub compare确认main是PR祖先，无漏同步提交；14/14历史Review线程resolved，PR OPEN/Draft。
- 冻结HEAD的免费RAG CI 39127768114、Contract Check 39127768141、Secret Scan 39127768109成功；真实API job为SKIPPED。
- main已包含#66、#89（3034b8f）、#90（fd1722c），继承结构化上传、当前价格治理、Cleaner/Parent-Leaf去重/过滤及完整性检查。两版fixture完整性定向测试均实跑。
- #69/#87/#88未修改或合入；#88仅参考资源说明。共享main的原有3份tsconfig修改与未跟踪部署文件保留。
- 历史Smoke 39108656690为failure，运行代码a87df83；历史5条needs_review未读取、结算、清理或混入本次账本。

## 施工前事实与风险

| 项目 | 核实结果与限制 |
| --- | --- |
| 时间预算 | 默认改写6000ms、共享检索deadline8000ms；multi_query角色默认10s，本轮无DB策略覆盖；不提高deadline |
| 重排 | `_compress_multi_query`仍按查询顺序同步调用`full_candidate_scores`；本轮未进入该路径，不能证明串行重排是实际超时根因 |
| Prompt | 总查询数3且原问题第一；源配置与隔离库Active均v1；候选只在隔离库建立v3 Draft |
| 诊断与账本 | 当前代码存在copy_context、阶段耗时、迟到span、reservation关联与usage结算；Fake覆盖通过，不等于真实MQ路径验收 |
| 评分可观测性 | 现有逐查询Trace只保留头部候选分数；不能冒充所有候选的完整评分矩阵 |
| 预算预占 | 预检只验证小额预占/释放，未覆盖完整阶段额度的连续调用与并发占额；该缺口导致本次Smoke未完成 |

## 唯一隔离Compose

- project：`rag-eval-20261018-pr52`；独立网络`rag-eval-20261018-pr52_default`。
- 独立卷：pgdata、replicadata、evaldata、cache，均带同一project前缀；未挂载共享业务数据卷。
- 服务：PostgreSQL/pgvector、真实只读价格副本、Redis、evaluator应用进程。现有fixture导入与EvaluationService走本进程同步链路，检索池与TraceWriter为进程内worker；无额外Celery任务依赖，未启动Kafka/Ollama/BGE或无关服务。
- PostgreSQL只映射`127.0.0.1:55453`，价格副本只映射`127.0.0.1:55454`；评测库`agent_memory_test`，业务兼容库同样独立。
- 限制实际回读：PG 0.75 CPU/1200MiB，价格副本0.25 CPU/512MiB，Redis0.25 CPU/128MiB，evaluator约2.75 CPU/4096MiB；合计约4 CPU、5936MiB。日志轮转10m×3。
- PG max_connections=120 / shared_buffers=256MB / work_mem=8MB；同步池min=1/max=2；异步池实际20+10；case并发1，MQ/rewrite/outer/inner线程池4/4/4/6。观测PG连接约11条，未触及连接上限。异步池保持当前代码默认，不能宣称已按#88覆盖所有生产参数。
- Redis maxmemory=96MiB / noeviction，无共享Redis连接。
- Docker Engine 29.2.1，Desktop实际12 CPU、8242642944 bytes内存（约7.68GiB），同时有其他共享容器。D盘余量约9.1GiB，未施加40GB独占磁盘配额。**不是独占4C8G/40GB生产等价验收**。
- Artifact包含脱敏Compose资源展开配置、实际container limits及docker stats。复用本机镜像、Python依赖、Git与tiktoken缓存，没有重复下载模型/浏览器或混入其他PR代码。

## 配置、Prompt与数据身份

- 仅以只读事务读取当前注册表必需的配置；不复制旧索引、业务数据、账本或数据库快照。
- 指定模型：Chat `doubao-seed-2.0-mini` / provider `custom-doubao-seed-2-0-mini`；Embedding `qwen3.7-text-embedding`、Rerank `qwen3.7-text-rerank` / provider `specialized-api`。
- 凭据在受控进程内捕获密文、以新隔离主密钥重新加密，运行时仍经现有DB Registry/credentials机制解析；未将API Key写入代码、Compose、日志或Artifact。隔离主密钥仅通过容器环境注入。
- 收尾发现资源展开配置的Compose扩展锚曾包含隔离库口令和隔离主密钥，已从本地导出文件移除并修正导出逻辑；未提交或上传该文件，源库主密钥未写入Artifact，API Key未明文落盘。以实际两份API Key、隔离口令和隔离主密钥对37个Artifact精确扫描，0命中；隔离卷与容器在证据导出后清理。
- 当前配置的Chat thinking参数为disabled，原样继承现有注册表，未合入其他分支的thinking代码。
- 原批准价格5条，版本`2026-10-cny-1` / 来源`official-cny-2026-10-17` / CNY，保留原生效时间和审批记录。隔离副本由本轮配置库实际物理复制，确认`pg_is_in_recovery()=true`；使用现有`load_registry_price_rows/build_price_snapshot/verify_pricing`完成校验，未伪造recovery或跳过价格治理。
- Prompt Lock与隔离库57项Active配置IN_SYNC；`rag.multi_query` Active v1/hash `d3ea6cc684a20879`，旧v2仍Draft；本轮新候选v3 Draft/hash `a9e9f66220217388`。通过现有请求级版本pin装载候选，不修改Active或发布状态。
- 本次报告固定了候选v3，但由于预算拒绝，RC-024/031根本未渲染/调用改写。不能把候选配置hash当作这两例实际Provider Prompt的执行证据。
- main和PR E0均8 cases，dataset_version=`5.0.0-unified`、suite_hash=`8edad96827b879c3`；Golden、required facts、阈值和官方评分器不变。
- 16份fixture离线完整性通过；新冷索引16成功/0失败，213条1024维向量；版本`rag_eval_kb-baseline-2026-09-18`，BM25 hash `265b605f4c53a11b`。每份fixture SHA256已导出。

## 唯一真实流水线与费用

- eval_run_id：`rag-local-pr52-20261018-440a08e-01`。
- Artifact根：`D:/tmp/rag-eval-20261018-pr52/artifacts/`；真实报告位于`eval_runs/rag-local-pr52-20261018-440a08e-01/`；日志`fixture-index.log`和`smoke.log`；逐调用账本`final-ledger.json`。
- 使用独占创建的一次性启动标记，冷索引一次、8-case Smoke一次；未重试整轮，也未再次付费。
- 冷索引：78 calls / 91820账本tokens / CNY0.107015，78 settled，差额0。
- 全轮：**79 calls / 91824账本tokens / CNY0.107017**，79 settled，0 reserved、0 needs_review、0 unpriced、0 unattributed，usage与结算差额0。
- 34条Chat为Provider实测usage，73501 tokens / CNY0.097842；45条Embedding为明确标记estimated的18323 tokens / CNY0.009175。费用事实等级为**ESTIMATED**，不是全部Provider精确Token/费用；统一账本VERIFIED，账本envelope WITHIN_LIMITS。
- 预算日/月硬限CNY0.50；calls/tokens在两阶段交接时扣除索引已记用量。未提高总上限。

## 真实失败与RC-024/031诊断

| 项目 | 结果 |
| --- | --- |
| 官方终态 | strict gate FAIL，进程退出1 |
| 样本分母 | 8例保留：1 pass / 7 error / 0 skipped |
| RC-001 | 检索有结果，但Rerank在11ms内因QuotaExceeded失败并透传，无有效重排分数 |
| RC-024 | 在前置检索/Embedding预占处QuotaExceeded，未进入改写、逐查询召回或评分；无采购/质检有效排序证据 |
| RC-031 | 同上；连续低分、安全事故、退出及处置证据均未得到本轮真实排序验证 |
| 评分覆盖率 | 有有效Provider重排评分的case为0/8；仅1/8产生检索质量指标，7/8 error；全部8例真实链路均未完整完成 |
| Top-1 / Multi-hop | 官方报告缺失，不能判定达标 |
| Recall@5 / MRR | 官方均1.0，但仅基于RC-001的退化结果，不能当8例质量提升 |
| Precision@5 / Context Noise@10 | 官方0.25 / 0.75，同样只有1例覆盖，不能忽略噪声或缺失分母 |

### 有证据的根因

1. `RequestBudget.reserve()`每次预占整个阶段max_cost，不扣本请求已结算成本。
2. 索引已用0.107015元；评测阶段上限按剩余总额度设0.392985元。首个Embedding只花0.000002元、已完成结算后，日账本used变为0.107017元。
3. 后续仍申请0.392985元，合计0.500002元，超过0.50硬限，被正确的PG额度门禁拒绝。即使实际只有0.107017元消费、reserved为0，也无法继续。并发时同一整段额度的多个预占还会竞争，本轮配置不能容纳它们。
4. 本轮免费预检的小额reserve/release不能证明真实阶段额度可执行；这是环境预算规划/预检缺口，不应归咎于RAG排序算法。
5. RC-024/031未调用Provider，无法提供其Provider推理/网络/线程排队分段耗时、finish_reason、真实completion tokens或逐查询评分；均明确UNVERIFIED，不用旧Smoke补齐。

索引阶段另观察到一次元数据本地30s等待超时，迟到Provider结果约45647.6ms、2430 tokens仍真实结算（usage id=2）。这证明索引路径存在等待与完成时间差，但不能据此推断MQ的6000ms窗口或串行评分根因。

## 最小修复与免费证据

- 只修改`backend/infra/llm/budget.py`：正数上限的预占额改为max_cost减本请求已记录成本；仍在原锁内执行、沿用原PG治理/身份/Trace/结算，不调整调用数、模型、价格、Prompt、阈值或deadline。
- 仍保守预占全部请求剩余额度，不释放在途/needs_review。该修复消除连续调用对已花费额度的重复预占，**没有解决紧预算下的并发容纳问题，也不能声称修复后Smoke会通过**。
- 新增回归覆盖primary/retry/fallback连续调用、未决竞争仍拒绝及请求成本硬限；修复前4 failed/1 passed，修复后相关组合182 passed / 33.81s。
- 独立免费库`agent_budget_regression_test`实际跑Fake Provider + PostgresQuotaStore：连续调用、重复settle幂等、两线程竞争仅允许1个、needs_review保留预占；四个主体/周期账本均used0.107020+reserved0.392980=0.50。无Provider调用，不触碰真实评测账本；结果`free-budget-pg.json`。
- 修复前main相关免费测试56 passed / 37.67s；目标PR相关184 passed / 32.86s；补充账本/评测55 passed / 19.02s；Prompt Lock、Contract Lock、E0、增量Secret Scan通过。修复后剩余免费检查及最终CI结果以交付补记为准。
- 修复后补充流式/计费、全部Prompt、16-fixture、评测与路径门禁组合283 passed / 45.30s；出现1个既有TestCase collection warning及1个Windows Proactor收尾warning，无skip/xfail。
- T0：两份Python文件py_compile通过；新增测试完整Ruff通过，修改的预算文件定向E9/F63/F7/F82检查通过；完整Ruff仍有冻结基线已存在的W291/I001/N818共3项，未改异常公共命名或掩盖为完整lint通过。git diff --check通过。
- 本地新增Diff自查确认request成本与PG结算都使用CNY/六位精度，预占计算仍在原RLock内；PG仍管理在途及needs_review占额。没有并发改造，没有修改模型/Prompt/Golden/deadline或调用账本出口。新回归测试已接入现有免费PR工作流，付费job/触发器未变。
- 文档同步：本文件及`docs/governance/llm-budget-billing.md`说明剩余额度预占与并发保守边界。

## 未完成与下一步

- 不再付费。RC-024/031逐查询诊断、多查询质量、Provider实际迟到结算路径仍未通过本轮真实验收。
- 下一次真实评测需用户另行授权；应先用Fake Provider+真实PG按完整阶段额度模拟冷索引后连续/并发调用，证明在0.50元总硬限下可执行。不能靠抬高硬限、减样本、换模型或改分母追分。
- PR维持Draft；不合并、不发布Prompt。预算修复的真实Provider验收未执行，MERGE_READY=false。
