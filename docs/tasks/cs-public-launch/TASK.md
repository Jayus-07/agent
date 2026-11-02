# 任务：客服域公开投产实施（cs-public-launch，供执行会话接手）

- 任务 ID：`cs-public-launch`
- 来源：2026-11-02 用户拍板「写公开投产实施计划，下一会话处理」；承接 `cs-evaluation-upgrade`（P1–P8 已收官，评测运营线达成受控试用口径）
- Worktree：**已存在，下一会话必须复用** `D:\Program Files\workplace\agent-cs-prod`、`feat/cs-prod-q3`；保留未提交改动。初始Q1从origin/main创建的步骤已完成，勿在agent-gov或主目录改本任务。
- 分支策略：每期独立分支 `feat/cs-prod-qN` 独立 PR；纯文档/计划分支 `docs/cs-prod-*`。**合并前显式逐项确认全部 CI 结论**（含 skipping 项核对是否非 RAG 预期）
- 状态：**Q1–Q6 全部完成并合并（2026-11-03）；受控试用放量达成；全量公开投产待两拍板项（见 Q6-FUNDS-DECISION.md）与主链路冒烟**

---

## 1. 背景与现状（免重探，全部 2026-11-02 实证）

- cs-evaluation-upgrade 全部完成：320→324 锁版（v2.2）、离线门禁入 CI（intent≥0.74/target≥0.79/risk≥0.78）、cs 数据集注册进管理端（dataset_key=`cs_locked_v2_2`，published v1.0.0 = 324 items）、case 归档端点上线（398 竞态 case 清零）、持久化 intake 上线、SOP 入 evaluation-governance.md §1。
- **live 基线（legacy CLI，324 条全量）**：v1 20 条 9/20（与离线逐条一致）、CS2 304 条 188/304=61.8%（faq 88.3% / complaint 85.0% / query 72.6% / safety 57.5% / **action 38.7%** / **multi_turn 22.5%**）；成本 ≈1.05 元。
- **v2 平台对非 rag 硬编码 live=False**（`evaluation_v2/runner.py:414`，cs ∈ CONTRACT_ONLY_RUNNER_MODULES）——管理端发不起 cs live run，live 唯一通道 = legacy CLI `python -m backend.evaluation cs --live`；`evaluation_mode` 非 rag 只存快照不消费，Q1已隐藏非RAG方式选项并固定发送offline；该修复不等于接通非RAG live。
- **P6历史现象（Q2已纠正根因）**：101/304「系统繁忙」发生于宿主 local 空索引路径；生产 remote 索引存在且可检索，未重建共享索引。Q2修正宿主RAG/业务地址后60条繁忙0/60，但整体质量门禁仍FAIL。详见Q2-VALIDATION.md。
- 客服域投产就绪度（2026-11-01 评估）：功能/质量面达标可受控试用；硬缺口=容量压测零数据、安全渗透未做、资金真实后端范围决策。
- 真实渠道：**web（用户已确认）**；channel 枚举其余值（app/phone/wechat）接入哪个验哪个。
- 宿主执行三件套（live CLI/宿主进程跑批前置）：①8088 模型服务（`dev-local-models.bat`）；②`NO_PROXY="*"`；③`refresh_registry()`。已封装 `d:/tmp/cs-live/run_eval.py`（不入库，复用先提炼）。
- 环境坑沿用 cs-evaluation-upgrade TASK §2.3（Mimosa hook 拦写 / GitHub 抖动退避重试 / compose 等价原命令 / MSYS_NO_PATHCONV / 真库 5433 / 并行会话纪律）；身份链（user:279 super_admin JWT + agent-auth-redis 会话键 + X-API-Key 经 :9080）见 cs-evaluation-upgrade PROGRESS 2026-11-02（四）。

## 2. 总计划（Q1–Q6，按序执行；Q3已拍板，Q6待拍板）

下表成本为立项时粗估，不是当前剩余工期承诺；最新进度、扩大后的Q3范围与验收路径见HANDOFF.md。

| 期 | 内容 | 成本 | 依赖 |
|---|---|---|---|
| Q1 | 上线巡检小件：管理端非 rag 评测方式选项处理 + frontend-cs 生产构建验证 + 渠道/资金落档 + 共享库残留清理 | ~1 天 | 无 |
| Q2 | rag-service cs_faq BM25 索引补建 + live 冒烟对比（「系统繁忙」101/304 → ≤5%） | ~半天 | 无 |
| Q3 | live 质量缺口决策与实施：full_path 断言接线或降口径、action 38.7% 缺口拍板、multi_turn 逐轮重放（runner v2.1）、live 常规频次/预算拍板 | ~1 天（拍板项视范围） | Q2（质量基线干净后再决策） |
| Q4 | 多坐席容量压测（当前零数据）：场景设计 → 执行 → 报告 | 1–2 天 | Q1 |
| Q5 | 安全渗透自检：Mimosa 深度扫描 + 客服域 RBAC/IDOR/租户边界复核（authorization-standard.md 口径） | ~1 天 | Q1 |
| Q6 | 资金功能首期范围决策落档（用户拍板；保持 disabled 即不阻塞 web 首期） | ~0.5 天 | Q1 |

**门槛口径**：
- **受控试用放量**（真实用户进 web 渠道）：Q1 + Q2 完成。
- **全量公开投产**：Q1–Q6 全部完成且验收矩阵全绿 + 主链路冒烟零回归。

## 3. 各期详情与验收

### Q1 上线巡检小件（开工第一件事）

- ①**管理端非 rag 评测方式选项**：`frontend-admin/src/components/workbench/evaluation/proto/RunsTab.tsx` 发起评测表单（:124-184 附近）——非 rag 模块隐藏 semantic/ragas 选项并固定offline；UI使用中性离线说明（旅游模块不是contract_only）；若用户拍板接线 live 则另立项，不在本任务默认范围。前端改动需 frontend-admin build 验证（node_modules 用 junction 复用主仓库）。
- ②**frontend-cs 生产构建验证**：`COMPOSE_PROJECT_NAME=agent docker compose -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.local.yml build frontend-cs` 后 `up -d frontend-cs`，验证 `:3300/login` 200。动容器前先 `docker ps`/`netstat` 核查归属（并行会话纪律）。
- ③**渠道决策落档**：`docs/domains/customer-service.md` 渠道段写入「真实渠道=web（2026-11-01 用户确认）；app/phone/wechat 未接入」。
- ④**资金开关记录**：落档业务网关 http / sandbox 的真实语义与 `CS_REFUND_REQUEST_MODE=disabled`；业务网关并非独立资金开关，不设置无效 disabled（不阻塞 web 首期；资金真实后端另立项）。
- ⑤**共享库残留清理**（逐项确认非活跃/无引用后处置，处置方式有疑义先报告）：
  - `ai.unanswered_questions` 实测 29 条（entry=16 intake 后原记录仍保留；日期跨 10 月，不能证明均为当日测试残留）；按 Q1-RESIDUALS.md 保留依据处理；
  - P6 副作用：`customer_service.tickets` eval_bot 名下 60 条工单（COMPLAINT-*/HANDOFF-* 前缀）、confirmation store eval 记录（实测均 expired/cancelled，无 pending）、checkpointer `eval-cs-*` 线程；
  - rag 残留 case `6e6e3daa`（dataset smoke_refuse_mvmdmqfr v1.0.0，2026-10-24）——它在 published version items 内，**P7 archive 端点会拒绝**；需确认该数据集与 CI/评测引用关系后处置（禁删被 rag_smoke.yml 或 suite 引用的对象）；
  - 候选层 398 条 approved 重复 candidates（promoted_case_id 指向已归档 case，无清理端点）——2026-11-02 用户确认保留历史审核记录，挂账候选发布过滤修复；现有 review_candidate 禁止覆盖 approved/rejected，不再次 reject、不直改审核结论；
  - govtest/p7v/p9g- 前缀测试数据确认非活跃后清。
- **验收**：管理端非 rag 发起评测表单无误导选项；:3300 生产容器健康；customer-service.md 渠道/资金段落档；残留清单逐项清零或标注保留依据。

### Q2 知识链路修复（已合并）

- 已核实生产remote索引可用，实际修复宿主local空索引路径与业务服务地址；未重建共享索引。PR #260已合并，复测与限制见Q2-VALIDATION.md，不重做该阶段。
- **验收**：知识链路回归测试绿；live 冒烟（v1 20 条 + CS2 抽样 ≥40 条）「系统繁忙」比例 **≤5%**（基线 101/304≈33%）；路由断言零回归。

### Q3 live 质量缺口实施（用户已拍板，暂停待续）

2026-11-02用户明确批准，不再采用旧版“降口径/另立项”建议：

1. action逐例复核后针对性实施；路由错才补词表，有业务依据的错标才走锁版修订，不为通过率改期望。
2. 本期补齐full_path断言和multi_turn逐轮重放并实际验收。
3. 先修复并验证预算保护，再周度人工一次、单次≤10元；不创建自动排程。

当前有未提交实现与局部测试，真实完整链路、action修复、统一回归、服务重建验收及PR/CI/合并均未完成。施工顺序、具体缺口、测试分层与运行现场以[下一会话交接计划](HANDOFF.md)为准。

- 词表改动须cs-gate与离线回放门禁零回归；数据集修订须升版本/hash/manifest notes。
- 预算须覆盖真实HTTP/PG并发、币种、预占上界、额度拒绝终态、跨服务归因与对账，不能只证明ContextVar存在。
- **验收**：上述批准项全部有实际行为证据；对应定向T0–T2通过，高风险审查证据齐全，CI逐项核对后PR合并；不把mock或局部PASS当完整投产验收。
### Q4 容量压测

- 现状零数据，场景开工时设计：多坐席并发接入（坐席状态流转/认领/转人工）、SSE 长连接规模、`/agent` 主入口与 cs 并行负载、DB/Redis/APISIX 容量；4C8G 档位以 `部署/prod-4c8g/README.md` 为准。
- 工具复用本机已有依赖，不引入未要求框架；压测脚本与报告落 `docs/operations/` 或任务目录（评审后定）。
- **验收**：压测报告含场景、并发数、P95 延迟、错误率、瓶颈与结论；发现的瓶颈分级（阻塞投产/可挂账）。

### Q5 安全渗透自检

- 自检范围（不替代外部渗透，公开投产前外部渗透是否引入由用户拍板）：①Mimosa 深度安全扫描（全仓；开工核对可用性，本会话未发现对应可调用工具/命令，不能预设已具备）；②客服域边界复核：`/cs/*` 与 admin API 的 RBAC/IDOR/租户隔离按 `docs/auth/authorization-standard.md` 逐端点核对（P4–P8 新增的 evaluation_v2 端点全部 admin 门禁，纳入核对）；③SQL 参数化与只读权限抽查；④SSE/WebSocket 鉴权面。
- **验收**：扫描与复核报告落档；无未处置高危（发现的按风险分级：立即修 / 挂账+缓解措施）。

### Q6 资金范围决策落档

- 用户尚待拍板首期是否含真实资金动作。建议首期不含；必须核对退款申请、退款审核、商城支付等所有入口，不能把一个disabled记录视为全资金关闭。业务网关http/sandbox不是独立资金开关；真实资金后端若需施工另立项。
- **验收**：决策记录 + 开关状态核对一致。

## 4. 测试策略

- 遵循 testing-guide.md T0–T3 最小充分；安全/权限/状态/公共契约变化升 T2。
- 前端改动：frontend-admin 构建 + 既有评测中心测试回归；frontend-cs 构建以容器健康为验收。
- 词表/规则改动：cs-gate 三门禁（test_cs_eval_gate）+ 离线回放门禁（test_cs_decision_replay）零回归；阈值变动必须「阈值更新 + 依据」走 PR。
- 数据集修订：锁版流程 + manifest notes；不加新依赖。
- 压测/渗透产物为报告类交付，不入 CI；阻塞项修复带回归测试。
- 不以扩大 mock、删断言、skip 掩盖失败；未实际执行的验证不得报通过。

## 5. 会话交接（当前入口）

先读[HANDOFF.md](HANDOFF.md)，再核对本TASK与PROGRESS。**复用**`D:/Program Files/workplace/agent-cs-prod`、`feat/cs-prod-q3`，HEAD `144aea7729b4e573b87e5ba34421c42e329c98d2`，保留全部未提交与忽略文件；不要再执行Q1新建工作树步骤。

当前PAUSED；本会话仅整理交接，下一会话收到继续/接手授权后再施工。恢复前核对Git、上游、构建句柄、隔离资源、共享归属，按HANDOFF逐步验证；不stash/reset/清理，不重复启动仍活着的构建或付费批次。Q3决策已确认，不重复询问；Q6尚需拍板。
## 6. 决策点与风险

| 决策点/风险 | 处置 |
|---|---|
| 非 rag live：管理端与CLI区分 | 管理端固定offline、按后端模块能力解释；legacy CLI full_path/多轮属于本期Q3已批准范围，不混为另立项 |
| 残留清理误删共享对象 | 逐项先查引用（CI workflow、suite binding、dataset 发布指针），有疑义先报告 |
| rag-service/共享容器归属 | rebuild 前必查 `docker ps`/inspect；并行会话在用则改宿主执行或等待 |
| 压测对共享栈的冲击 | 用错位端口/独立容器名/独立 compose 项目，不与主栈抢绑定；压测窗口避开并行会话全量 pytest |
| 渗透自检的边界 | 只读自检 + 受控验证，不做破坏性利用；外部渗透是否引入由用户拍板 |
| 资金范围 | Q6必须用户拍板并核对全部入口，不能只凭退款申请disabled宣称关闭所有资金动作 |
| 基线漂移 | live 基线以 P6 数字为参照；变化以「数字 + 依据」走 PR 记录 |

## 7. 持续实施计划（2026-11-02 优化）

目标保持不变：Q1+Q2 达到受控试用放量条件；Q1–Q6 全绿并完成主链路冒烟后才可宣布公开投产。按 Q1→Q2→Q3→Q4→Q5→Q6 实施，不以写出计划替代实施。

| 阶段 | 最小实施路径 | 完成证据与停止条件 |
|---|---|---|
| Q1 | 修正非 RAG 表单与提交参数；组件真实渲染验证；两个前端生产构建；渠道与资金语义落档；逐项盘点残留 | 切换 RAG/CS 的 UI 与请求断言通过；生产容器健康；每类残留清零或有可核实保留依据；PR 合并 |
| Q2 | 先核对 cs_faq 发布指针、fixture、索引世代与现有导入入口；只补缺失索引，避免无依据重建整个知识库 | ≥60 条 live 冒烟、繁忙率≤5%、路由门禁零回归；保留批次与成本证据 |
| Q3 | 基于已批准范围逐例修action、本期补齐full_path与multi_turn、预算先行；详见HANDOFF | 决策已确认；真实链路与预算验收、定向验证和CI全绿；不以修改期望掩盖缺陷 |
| Q4 | 独立实例/端口与测试身份；先低负载探测再阶梯加压，记录资源、P95、错误率、连接恢复与状态正确性 | 报告明确实测容量和瓶颈；达不到业务要求则标记未验收，不用 HTTP 200 代替容量结论 |
| Q5 | 开工核对 Mimosa 实际可用性；按对应安全 Skill 执行扫描，结合客服与评测 API、SSE/WS 做资源级授权验证 | 报告含范围、工具限制、有效发现与修复证据；没有工具时明确缺口，不宣称完成深扫 |
| Q6 | 汇总所有资金入口及真实开关状态，建议首期关闭资金写入，向用户拍板；按决定验证 | 决策与实际行为一致；超出首期的真实资金后端另立项，不默认为已完成 |

执行约束：
- 每期独立分支/PR；实现→实际测试→Diff自查→PR→逐项核对 CI（含 skip 触发与路径过滤原因）→合并→回填 PROGRESS。用户已授权本持续计划的逐期 PR 与合并，不重复请求例行许可。
- 共享服务先查 compose 工作目录、端口和活动连接；其他会话使用时采用宿主或隔离容器，不覆盖其构建。构建镜像也不代表已部署。
- 残留只读盘点先行：发布版本、suite、运行快照、审计/候选来源引用都需核对。无法证明无引用或非活跃时保留并写明依据，不为清零直接删库。
- 398 条 approved 候选按用户决定保留；过滤修复另立小 PR，不纳入本期清理施工。
- 实测已知偏差：unanswered 当前 29 条，日期跨 10 月，不能沿用“28 条、全部为当日测试批”预设；smoke_refuse_mvmdmqfr 的 published version 有 2 个 suite version 引用，必须保留；eval_bot confirmations 当前均 expired/cancelled；eval-cs-* checkpoint 为 0。
- Q3已明确批准，按本期完整实施范围执行；Q6仍必须等明确决策再实施依赖项；等待期间推进不依赖该决策的只读分析或验证。外部阻塞记录原因、已尝试证据与具体解法，不重复无效安装/测试。
- 最终逐项回填验收矩阵、PR/HEAD、T0–T3 实测、Review处理、运行版本及未处置风险；有必需缺口则保持 UNVERIFIED/BLOCKED，不宣布全量投产。