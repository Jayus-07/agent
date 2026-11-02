# 任务：客服域公开投产实施（cs-public-launch，供执行会话接手）

- 任务 ID：`cs-public-launch`
- 来源：2026-11-02 用户拍板「写公开投产实施计划，下一会话处理」；承接 `cs-evaluation-upgrade`（P1–P8 已收官，评测运营线达成受控试用口径）
- Worktree：**新建** `D:\Program Files\workplace\agent-cs-prod`（勿复用 agent-gov——已停靠 docs/cs-eval-p8-wrapup 使命完结；分支自最新 origin/main）
- 分支策略：每期独立分支 `feat/cs-prod-qN` 独立 PR；纯文档/计划分支 `docs/cs-prod-*`。**合并前显式逐项确认全部 CI 结论**（含 skipping 项核对是否非 RAG 预期）
- 状态：**计划已立项，Q1 待实施**

---

## 1. 背景与现状（免重探，全部 2026-11-02 实证）

- cs-evaluation-upgrade 全部完成：320→324 锁版（v2.2）、离线门禁入 CI（intent≥0.74/target≥0.79/risk≥0.78）、cs 数据集注册进管理端（dataset_key=`cs_locked_v2_2`，published v1.0.0 = 324 items）、case 归档端点上线（398 竞态 case 清零）、持久化 intake 上线、SOP 入 evaluation-governance.md §1。
- **live 基线（legacy CLI，324 条全量）**：v1 20 条 9/20（与离线逐条一致）、CS2 304 条 188/304=61.8%（faq 88.3% / complaint 85.0% / query 72.6% / safety 57.5% / **action 38.7%** / **multi_turn 22.5%**）；成本 ≈1.05 元。
- **v2 平台对非 rag 硬编码 live=False**（`evaluation_v2/runner.py:414`，cs ∈ CONTRACT_ONLY_RUNNER_MODULES）——管理端发不起 cs live run，live 唯一通道 = legacy CLI `python -m backend.evaluation cs --live`；`evaluation_mode` 非 rag 只存快照不消费，**但 RunsTab UI 仍展示 semantic 等选项（误导）**。
- **rag-service cs_faq KB BM25 索引缺失**：live 跑批 **101/304 返回「系统繁忙」兜底应答**的根因（runtime 只读索引不可用，基建缺口）；路由断言不受影响，知识应答质量被拉低。
- 客服域投产就绪度（2026-11-01 评估）：功能/质量面达标可受控试用；硬缺口=容量压测零数据、安全渗透未做、资金真实后端范围决策。
- 真实渠道：**web（用户已确认）**；channel 枚举其余值（app/phone/wechat）接入哪个验哪个。
- 宿主执行三件套（live CLI/宿主进程跑批前置）：①8088 模型服务（`dev-local-models.bat`）；②`NO_PROXY="*"`；③`refresh_registry()`。已封装 `d:/tmp/cs-live/run_eval.py`（不入库，复用先提炼）。
- 环境坑沿用 cs-evaluation-upgrade TASK §2.3（Mimosa hook 拦写 / GitHub 抖动退避重试 / compose 等价原命令 / MSYS_NO_PATHCONV / 真库 5433 / 并行会话纪律）；身份链（user:279 super_admin JWT + agent-auth-redis 会话键 + X-API-Key 经 :9080）见 cs-evaluation-upgrade PROGRESS 2026-11-02（四）。

## 2. 总计划（Q1–Q6，按序执行；Q3/Q6 含用户拍板点）

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

- ①**管理端非 rag 评测方式选项**：`frontend-admin/src/components/workbench/evaluation/proto/RunsTab.tsx` 发起评测表单（:124-184 附近）——非 rag 模块隐藏 semantic/ragas 选项（contract_only 终态口径）；若用户拍板接线 live 则另立项，不在本任务默认范围。前端改动需 frontend-admin build 验证（node_modules 用 junction 复用主仓库）。
- ②**frontend-cs 生产构建验证**：`COMPOSE_PROJECT_NAME=agent docker compose -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.local.yml build frontend-cs` 后 `up -d frontend-cs`，验证 `:3300/login` 200。动容器前先 `docker ps`/`netstat` 核查归属（并行会话纪律）。
- ③**渠道决策落档**：`docs/domains/customer-service.md` 渠道段写入「真实渠道=web（2026-11-01 用户确认）；app/phone/wechat 未接入」。
- ④**资金开关记录**：落档 `CS_BUSINESS_GATEWAY_MODE` / `CS_REFUND_REQUEST_MODE` 保持 disabled 及其含义（不阻塞 web 首期；资金真实后端另立项）。
- ⑤**共享库残留清理**（逐项确认非活跃/无引用后处置，处置方式有疑义先报告）：
  - `ai.unanswered_questions` 余 28 条测试残留（entry=16 已被 P8 intake 消费 reject，其余为 2026-11-02 测试批）；
  - P6 副作用：`customer_service.tickets` eval_bot 名下 60 条工单（COMPLAINT-*/HANDOFF-* 前缀）、confirmation store eval 残留（地址修改 pending）、checkpointer `eval-cs-*` 线程；
  - rag 残留 case `6e6e3daa`（dataset smoke_refuse_mvmdmqfr v1.0.0，2026-10-24）——它在 published version items 内，**P7 archive 端点会拒绝**；需确认该数据集与 CI/评测引用关系后处置（禁删被 rag_smoke.yml 或 suite 引用的对象）；
  - 候选层 398 条 approved 重复 candidates（promoted_case_id 指向已归档 case，无清理端点）——方案：经 `POST /v2/candidates/{id}/review` 逐条 reject 留痕（372 行 review 端点已存在），或评估后补候选清理端点（另走小 PR）；
  - govtest/p7v/p9g- 前缀测试数据确认非活跃后清。
- **验收**：管理端非 rag 发起评测表单无误导选项；:3300 生产容器健康；customer-service.md 渠道/资金段落档；残留清单逐项清零或标注保留依据。

### Q2 BM25 索引补建

- rag-service cs_faq KB 索引重建（P6 探明入口 `index/import_fixture`；开工时以 rag-service 实际代码/fixture 为准）；注意 rag-service 容器属共享栈且为并行会话构建——rebuild 前核查归属。
- **验收**：知识链路回归测试绿；live 冒烟（v1 20 条 + CS2 抽样 ≥40 条）「系统繁忙」比例 **≤5%**（基线 101/304≈33%）；路由断言零回归。

### Q3 live 质量缺口决策与实施（含拍板）

- **拍板项（开工时向用户提出，给出建议项）**：
  1. action 38.7% 缺口：接受「低置信度→知识兜底」为设计行为并修订数据集期望（走锁版流程），或扩规则词表补齐（经 cs-gate fail-closed 护航）——**建议**：先扩词表实测，残余修订期望；
  2. full_path 断言（must_not_contain/forbidden_facts/clarify 唯一消费路径无调用方）：加开关接线或正式降口径——**建议**：受控试用先降口径挂账，接线另立项；
  3. multi_turn 逐轮重放（runner v2.1）：**建议**：与 full_path 接线合并评估，不阻塞投产；
  4. live 常规频次/预算：**建议**：受控试用期间周度一次、单次预算 ≤10 元（P6 实测 1.05 元/324 条）。
- 实施部分经 cs-gate 三门禁护航（vocab_gate fail-closed）；数据集修订走锁版流程（升版本 + 重算 sha256 + manifest notes）。
- **验收**：拍板结论全部落档（PROGRESS 决策记录段）；实施项 CI 显式全绿、门禁零回归。

### Q4 容量压测

- 现状零数据，场景开工时设计：多坐席并发接入（坐席状态流转/认领/转人工）、SSE 长连接规模、`/agent` 主入口与 cs 并行负载、DB/Redis/APISIX 容量；4C8G 档位以 `部署/prod-4c8g/README.md` 为准。
- 工具复用本机已有依赖，不引入未要求框架；压测脚本与报告落 `docs/operations/` 或任务目录（评审后定）。
- **验收**：压测报告含场景、并发数、P95 延迟、错误率、瓶颈与结论；发现的瓶颈分级（阻塞投产/可挂账）。

### Q5 安全渗透自检

- 自检范围（不替代外部渗透，公开投产前外部渗透是否引入由用户拍板）：①Mimosa 深度安全扫描（全仓，工具已在环境内）；②客服域边界复核：`/cs/*` 与 admin API 的 RBAC/IDOR/租户隔离按 `docs/auth/authorization-standard.md` 逐端点核对（P4–P8 新增的 evaluation_v2 端点全部 admin 门禁，纳入核对）；③SQL 参数化与只读权限抽查；④SSE/WebSocket 鉴权面。
- **验收**：扫描与复核报告落档；无未处置高危（发现的按风险分级：立即修 / 挂账+缓解措施）。

### Q6 资金范围决策落档

- 用户拍板首期是否含资金动作；不含则 Q1④ 的 disabled 记录即为终态，含则资金真实后端另立项（不在本任务施工）。
- **验收**：决策记录 + 开关状态核对一致。

## 4. 测试策略

- 遵循 testing-guide.md T0–T3 最小充分；安全/权限/状态/公共契约变化升 T2。
- 前端改动：frontend-admin 构建 + 既有评测中心测试回归；frontend-cs 构建以容器健康为验收。
- 词表/规则改动：cs-gate 三门禁（test_cs_eval_gate）+ 离线回放门禁（test_cs_decision_replay）零回归；阈值变动必须「阈值更新 + 依据」走 PR。
- 数据集修订：锁版流程 + manifest notes；不加新依赖。
- 压测/渗透产物为报告类交付，不入 CI；阻塞项修复带回归测试。
- 不以扩大 mock、删断言、skip 掩盖失败；未实际执行的验证不得报通过。

## 5. 会话交接（启动清单）

1. 核对：`git -C "D:\Program Files\workplace\agent-cs-prod" fetch`（worktree 需新建：`git worktree add "D:/Program Files/workplace/agent-cs-prod" -b feat/cs-prod-q1 origin/main`，从 agent-gov 或主仓库执行均可）；worktree 无 .env 时从主仓库拷贝。
2. 通读本 TASK + PROGRESS.md；P6/P7/P8 结论与环境经验见 cs-evaluation-upgrade PROGRESS 2026-11-02（四）（六）（七）（八）。
3. Q1 开工：巡检小件按 §3 顺序执行 → 每项实测留证 → diff → PR → CI 逐项核对 → 合并 → PROGRESS 回填；随后 Q2–Q6 同流程。
4. 暂停时按仓库规范记录 PAUSED 现场（HEAD/未提交/测试证据/下一步）；勿动其他会话 worktree 与共享容器。

## 6. 决策点与风险

| 决策点/风险 | 处置 |
|---|---|
| 非 rag live：隐藏选项 vs 接线 | 本任务默认隐藏选项（contract_only 终态）；接线另立项 |
| 残留清理误删共享对象 | 逐项先查引用（CI workflow、suite binding、dataset 发布指针），有疑义先报告 |
| rag-service/共享容器归属 | rebuild 前必查 `docker ps`/inspect；并行会话在用则改宿主执行或等待 |
| 压测对共享栈的冲击 | 用错位端口/独立容器名/独立 compose 项目，不与主栈抢绑定；压测窗口避开并行会话全量 pytest |
| 渗透自检的边界 | 只读自检 + 受控验证，不做破坏性利用；外部渗透是否引入由用户拍板 |
| 资金范围 | 纯用户拍板；disabled 即不阻塞 web 首期 |
| 基线漂移 | live 基线以 P6 数字为参照；变化以「数字 + 依据」走 PR 记录 |
