# 全平台标准化：首轮 GitHub 静态盘点

> **审查性质**：2026-10-11 的 GitHub 远程主线树与选定代码文件的**初步静态审查**，不是已完成的全仓逐文件审查，也没有调用本地 Docker、真实数据库、Provider、浏览器或全部测试。审计基线：`main@47731825326a70b5dd353d7d093e4a80349bfce6`。本报告中的问题按证据强度区分“已确认偏差”和“待验证候选”；凡涉及业务安全/生产就绪均仍需代码走查和定向实验。

## 1. 仓库资源范围（Git Tree 统计）

| 位置 | 已跟踪文件数 | 判断 |
| --- | ---: | --- |
| 全仓 | **5,891** | 非源码文件比例较高，规模本身不是错误 |
| `backend/` | **2,673** | 含生产代码、测试、数据和 94 个迁移文件 |
| `tmp/` | **1,517** | 大量 `pytest*`、`pt_*` 生成证据，需确认是否可停止跟踪 |
| `data/` | **762** | 包含 quality_records、docs、seed、迁移等混合数据，**不能整目录删除** |
| `frontend/` | **243** | 用户端 |
| `frontend-admin/` | **272** | 管理端 |
| `frontend-cs/` | **172** | 客服端 |
| `scripts/` | **79** | 含通用工具与单次排障脚本 |
| `docs/` | **82** | 本段计数是 main 的历史基线，不包含 PR #21 新增规范 |

`.gitignore` 已包含 `tmp/`，但 **Git Tree 仍跟踪其文件**：新增忽略规则不能取消已提交文件的跟踪。未来清理需先检查测试数据依赖、归档与本地工作树，再考虑单独 PR 的停止跟踪操作。

## 2. 已确认 / 建议归类

| ID / 风险 | 标准对照与真实证据 | 归类 | 下一步 |
| --- | --- | --- | --- |
| F-01 / P1 | `backend/app/api/router.py` 的 from-import 清单 60 个条目，`admin_tasks`、`approvals`、`cs_admin`、`cs_agent_ws` 各导入两次 | **FIX** | 只去重导入，核验已注册 Router 及 OpenAPI 行为不变 |
| F-02 / P1 | `tmp/` 1,517 个文件已进入 Git Tree，`.gitignore` 声明忽略 `tmp/` | **REVIEW → MOVE/DELETE 候选** | 生成物溯源、消费者引用、可恢复备份、确权后按批停止跟踪；暂不删除 |
| F-03 / P1 | `data/` 已跟踪 762 个，`data/quality_records` 430、`data/docs` 216、`data/seed` 44 | **REVIEW** | 划分“不可再生黄金集/fixture”“示例种子”“私人或运行产物”，先建立清单与保护策略 |
| F-04 / P1 | 三前端共有 **67 个相同相对路径**，其中 **23 个是三份字节 SHA 完全相同**，包括 `src/lib/sse-parser.ts`、`src/lib/fetcher.ts`、`src/components/shared/Toast.tsx` 等 | **MERGE 候选** | 用消费者与测试确认真实跨端复用条件；先抽无状态纯函数；授权/页面行为保持各自边界，禁止全量机械合并 |
| F-05 / P1 | 三个前端 `src/api/client.ts` 体积与 SHA 不同；都自称“唯一网络入口” | **REVIEW / KEEP 暂定** | 对照 401 refresh、超时、后端业务错误码、SSE 与权限，先证明差异意图后再抽共享协议 |
| F-06 / P1 | `.github/workflows/` 目前 4 个工作流，历史 [测试资产治理审计](../test-asset-governance/AUDIT.md) 同样报告无 `pytest` CI 门禁 | **FIX 候选，需 CI 验证** | 单独设计轻量 T0/T1 分层门禁，不在文档提交里声称现有 CI 已测全仓 |
| F-07 / P1 | `backend/config/observability.py` 的 `TRACE_DETAIL_LEVEL` 默认为 `full`；`backend/observability/redaction.py` 的 full 原样返回；遮罩异常时可能返回原文本 | **REVIEW · 数据安全** | 核对 Trace 写入原文位置、调用者权限、环境变量、留存范围与 masking 异常分支；不以默认配置推断已向未授权用户泄漏 |
| F-08 / P2 | `scripts/check_docs.py` 实际是查询 `data/doc_registry.db` 的分类诊断脚本，并非 Markdown 链接检查器；`docs/README.md` 曾依赖人工链接验证 | **FIX 候选** | 判断脚本调用方和历史用途，避免命名误导；如需真正 lint docs，应新建专用静态检查且不读取业务 DB |
| F-09 / P2 | `pyproject.toml` 的分层注释仍写“分层规则（CLAUDE.md）”，当前跟踪树未见该文件；`pytest.ini` 和 `pyproject.toml` 同时包含 pytest 设置 | **REVIEW** | 历史注释与真实生效配置对账；pytest.ini 有优先级，改配置前运行收集和最小用例 |
| F-10 / P2 | `backend/app/api/routes/observability.py` 文件头仍称 SQLite TraceStore，而现行 [Trace 文档](../../observability/trace-model.md) 指向 PostgreSQL | **FIX 候选** | 核对 TraceStore 当前实现后更新过时 docstring，不能据注释改存储 |
| F-11 / P2 | `backend/orchestration/workflow/` 是引擎（decorator/executor/registry）；`workflows/` 是流程定义与 register_all；实证职责不同 | **KEEP** | 不做目录合并，新增 Workflow 继续遵循注册单一事实源 |
| F-12 / P2 | `backend/tools/tool_registry.py` 的工具发现与 `backend/orchestration/capability_registry.py` 的能力注册用途不同 | **KEEP** | 不合并 Tool 与 Capability 两层 |
| F-13 / P2 | `backend/eval/metadata_baseline/` 提供元数据离线评测；`backend/evaluation/` 是通用评测中心 | **KEEP 暂定** | 如需改名先证明引用与迁移价值，避免纯名称驱动 |
| F-14 / P2 | `backend/travel_v2/` 是 V2 Trip/Revision 接口与持久服务；`backend/travel/` 是既有旅游规划/外部依赖 | **KEEP** | 不在目录重构中混合两套不同生命周期 |
| F-15 / P2 | `backend/selection/` 与 `backend/selection_funnel/` 已见不同业务职责（推荐/评分与漏斗图），未查到足够的重复证据 | **REVIEW / KEEP 暂定** | 调用图与数据仓储对账后决定是否提取共享数据服务 |

以上 15 项中 **明确可先原地修正的为 F-01**；F-02～F-10 是候选，不等于可直接删除或合并；F-11～F-15 有待保留的边界证据。已跟踪文件数和 SHA 为远端 Git Tree 得出；没有运行 lint、单测或 E2E。

## 3. 尚未完成的全仓审计范围（不得勾 PASS）

| 审核流 | 应做的代码级与运行级核对 | 状态 |
| --- | --- | --- |
| 网关/API/认证 | 所有入口与路径权限、IDOR、租户头可信边界、接口 OpenAPI 差异 | **NOT VERIFIED** |
| 主 Router/Graph/Workflow | 注册/动态导入/选择路径、Checkpoint、handoff 和失败传播 | **仅抽样静态阅读** |
| Tool/MCP/Provider | 全部 Tool 入口是否经过治理、200+failed、超时/回退、写操作未知 | **仅抽样静态阅读** |
| RAG/SQL/Memory/Prompt/预算 | 读写授权、证据门、索引、上下文与 token/计费归因 | **NOT VERIFIED** |
| 客服/旅游/选品 | 状态机、并发 CAS、跨域复用、审批/Provider、业务最终结果 | **仅抽样静态阅读** |
| 三端 UI 与接口 | 同路径副本对账、API/SSE 错误、权限回归、移动端、版本编辑 | **未运行前端测试/浏览器** |
| 数据/迁移/异步 | DB migrations、Worker 重投/恢复、数据保留与目录迁移 | **NOT VERIFIED** |
| CI/质量/部署/观测 | pytest/Vitest/容器/告警真实运行与部署身份 | **未执行真实环境验收** |

### 必须避免的误判

- 不以 23 组前端相同 SHA 证明“共享包马上可上线”；存在 app-local 测试、别名解析、主题、权限和不同 Next.js 部署等复杂性。
- 不以 `tmp/` 的名称判断所有临时目录均可删除；可能存在脚本、回归黄金数据或曾供 Worktree 恢复的证据。
- 不以本次审计未发现安全问题宣称没有安全漏洞。鉴权、跨租户、写操作、Trace 必须做真实定向验证。
- 没有对全 5,891 个文件逐行阅读、没有查本机 39 个工作树当前状态，**不允许直接执行大规模 git mv 或 rm**。

## 4. 优先建议施工批次

**Batch A（推荐第一批）**：清理 API 聚合的四个重复导入；修正经核对的过期注释；增加无 DB 副作用的 T0 文档校验器（需先与同名旧脚本区分）；构建“运行产物候选清单”，不删除文件。

**Batch B**：三个前端优先审查 `sse-parser` 等纯函数与通用类型；先一致性测试和消费端构建，再抽取共享实现（如果收益超过增加 workspace/tooling 的维护成本）。

**Batch C**：按业务域和横切契约逐个对账：API/权限 → Runtime/Tool/Provider → RAG/SQL/CS/Travel/Selection → 数据/任务 → 观测/发布；每批修复独立提交及定向测试。

**Batch D**：仅在引用和实际数据归属核清后分批移出 `tmp/` 已跟踪生成物、调整 `data/` 持久/fixture/运行时分类；需要用户确认批量删除范围与归档方式。

施工流程遵循 [安全重构手册](../../development/refactoring-playbook.md)。本报告为基线，后续每批补充代码位置、测试命令/结果、变更 SHA 和剩余风险；不允许将尚未验收项改为“全仓已统一”。