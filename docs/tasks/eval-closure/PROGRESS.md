# PROGRESS: 评测闭环收口（eval-closure）

- 状态：**IN_PROGRESS（收尾项）**；计划批次已合并，预算上下文修复已在独立工作树实现并完成本地验证、PR 待创建；真实浏览器 + APISIX JWT 写入链路仍 **UNVERIFIED**。
- 本次验收记录：`agent-wt-eval-f-acceptance` / `codex/eval-closure-f-api-acceptance`（从 `origin/main` 创建；原 F/D3/D4a worktree 保留）。
- 当前主线核对基线：2026-11-05 `origin/main=040453a`（包含 PR #332）；F API 验收 worktree
  起始基线为 `8798dca`。任务沿革基线见 TASK.md。

## 并行协调记录

- 2026-11-04：撞车核对完成。`eval-center-simplify`（worktree `agent-eval-simplify`，
  Phase D IN_PROGRESS）冻结区=本任务范围，任务不撞车；但 F1-F3 与其共用三个组件文件，
  **前端批必须等其合并**。B1-B3 后端批无文件交集，可先行。
- 2026-11-04：审计 P3 收官批当日全部完成（PR #304-#316），本任务排在依赖批次之后，
  E 批（B1-B3）随时可开工。

## 进度

- [x] B1 表现阈值入门禁（PR #326；定向测试通过）
- [x] B2 在线采样评测器（PR #326；一次 trigger=online 落库实测）
- [x] B3 Celery Beat 调度注册（PR #326；beat 注册实测）
- [x] F1 RunsTab：Gate 状态/审批/来源徽标（PR #328；隔离 API POST 201，GET 读回 1 条审批）
- [x] F2 DatasetsTab：反馈候选审核（PR #328；隔离 API approve/reject/promote 均成功，状态与 JSONL 读回正确）
- [x] F3 CompareTab：人工评审挂载（PR #328；隔离 API POST 201，GET 读回 1 条人工评分）

## 验证边界

- 共享业务库保持只读；隔离 API 验收数据写入独立临时 Postgres/Redis 容器和容器内临时 JSONL，验收后容器、匿名卷及主机临时脚本/环境文件均已删除。
- 隔离服务使用测试 API key 和 `X-Auth-Type=jwt`、`X-User-Roles=super_admin`、租户等网关契约头，实际经过 API key 中间件、路由依赖、角色检查、数据库事务及幂等存储；该侧车调用没有经过 APISIX，也没有验证真实 JWT 签名。
- 组件测试与 `tsc` 已按 F 批记录通过；本次没有在浏览器中点击管理端写操作。故“路由写操作闭环”已验证；APISIX JWT + 浏览器的联合链路未声称通过。

## 下一步

- 本任务计划中的 E′、D1、F、D3、D4a 均无剩余实现批次。
- ECS 三项与 pillow/filelock/dnspython/unstructured 继续按既有裁决保持不动。
- 在线采样长期启用前，仍须单独裁决并补齐后台预算预占/结算链路（见 E′ 风险记录）。

## 2026-11-05 接手批续工（F / D3）

### F 批（管理端）

- PR [#328](https://github.com/Jayus-07/agent/pull/328) 已于 2026-11-05 合并，HEAD `27320474a0d53e766e2b7a7f93fb525a5a11320f`，merge commit `83aa74ca81a2c91774d084dfd41e80d059fe90a2`。
- 改动：RunsTab Gate 状态/审批/线上来源、DatasetsTab feedback 候选审核与晋升、CompareTab 人工评审挂载；新增三处组件测试。
- 定向测试 4 个组件文件 / 6 个测试通过；`tsc --noEmit` 通过；任务 worktree `devctl.bat rebuild /y` 完成，API / 管理端健康检查 200。
- PR #328 合并时，真实 super_admin 只读 API 的运行/反馈候选 GET 与 logout 均 200；因共享库没有安全写入 fixture，当时写操作保持 UNVERIFIED。后续隔离写操作 API 验收见本文“F 写操作隔离 API 验收”。

### D3：LangChain / LangGraph 依赖族

- 最新 `requirements-lock.txt` 已满足并超过原目标：langchain 1.4.7、langchain-classic 1.0.8、langchain-core 1.7.0、langgraph 1.2.16、langgraph-checkpoint 4.3.0、langgraph-checkpoint-postgres 3.2.0、langgraph-sdk 0.4.8、langsmith 0.14.12。该组升级已随供应链 PR #314 落入 main；本批不降级，也不产生重复 lock 改动。
- 2026-11-05 从 PyPI JSON 核对 8 个当前 pin：均未 yanked，`Requires-Python` 均兼容 Python 3.10。
- OSV：`pip-audit -r D:\tmp\eval-closure-d3-requirements.txt --no-deps --disable-pip --vulnerability-service osv`，8 个目标包 **No known vulnerabilities found**。该次只扫描目标直接包，不代表全锁 OSV 扫描。
- 全锁解析、PR CI、合并、重建、freeze 一致性和健康检查沿用 PR #314 与 `docs/tasks/audit-remediation-r1/PROGRESS.md` 已记录证据：368 包 dry-run 零 ERROR、11 checks pass、容器 freeze 368 包与 lock 零漂移、导入冒烟通过、`/health` 200×2。当前 app 容器实装版本与 lock 上述 8 个 pin 一致。
- D3 定向回归在当前 lock 的 Python 3.10 app 容器运行：RAG chain、pg_saver_pool、task checkpoint recovery、runtime checkpoint recovery、main graph checkpoint identity，**41 passed**。容器缺 Git，首次运行被测试数据快照保护挡住；最终运行设置 `RAG_EVAL_NO_ISOLATION=1` 并显式将 `RAG_DATA_DIR` 指向空目录 `/tmp/eval-closure-d3-data`，未读写共享 RAG 文件数据。checkpoint 用例按测试约定使用唯一 ID 并 teardown 清理。
- D3 结论：原目标升级已由 PR #314 完成；本批没有必要的 lock 变更。

### D4a：cryptography / datasets / oauthlib

- 当前 lock 已满足并超过原目标：cryptography 50.0.2（目标 50.0.0）、datasets 5.1.0（目标 5.0.1）、oauthlib 4.0.0（目标相同）；三项已随供应链 PR #314 合并，不做降级或重复 lock 改动。
- 2026-11-05 核对 PyPI JSON：三项均未 yanked；`Requires-Python` 分别为 `!=3.9.0,!=3.9.1,>=3.9`、`>=3.10.0`、`>=3.9`，兼容项目 Python 3.10 基线。
- OSV：`pip-audit -r D:\tmp\eval-closure-d4a-requirements.txt --no-deps --disable-pip --vulnerability-service osv`，3 个目标直接包 **No known vulnerabilities found**；本结果不代表完整 lock 的 OSV 扫描。
- D4a 定向回归在当前 lock 的 Python 3.10.22 app 容器运行：认证防爆破、本地认证、会话 family、RAG fixture ingest、dataset enterprise extensions、managed dataset execution、RAG eval suites，**76 passed（16.38s）**。session family 用例依约对随机用户做创建/级联清理。
- 新 FastAPI 0.161.1 使用延迟 router inclusion；原 authn 测试通过 `FastAPI.include_router().routes` 枚举不到 `_IncludedRouter` 里的 APIRoute。D4a 分支仅调整该结构测试为直接检查 `demo.router.routes`，仍断言两个 POST 路由有 `require_admin_user`，不改运行时授权。测试容器还缺仓库根 `docker-compose.yml`、部署 compose 与 `pyproject.toml`；临时复制这些只读测试输入后全组通过，结束后恢复 authn 原文件并清理临时副本。
- 八步链中的 lock 全量解析、PR CI、合并、rebuild/freeze 与 health 证据沿用 PR #314 / audit-remediation-r1 记录；当前 app 容器 `cryptography==50.0.2`、`datasets==5.1.0`、`oauthlib==4.0.0` 与 lock 一致。
- D4a 结论：目标版本已由 PR #314 完成；本批无必要的 lock 变更，PR 只包含 FastAPI lazy-router 兼容测试修正与验收记录。

## 2026-11-05 F 写操作隔离 API 验收

- 环境：一次性 Postgres 16 + Redis 容器，FastAPI `TestClient` 通过 ASGI HTTP 调用 API；只在隔离数据库中创建 PASS Gate、绑定 Human Rubric 的用例结果及两条反馈候选。
- F1：`GET /evaluation/v2/gates/{run_id}` 200；审批 `POST` 201；再次 `GET` 200，数据库读回 1 条审批。
- F2：候选列表 `GET` 200；approve、reject、promote `POST` 均 200；两条候选状态读回正确；promote 返回 `appended=true`，隔离数据集 `rag/cases.jsonl` 仅 1 行。
- F3：人工 Rubric `GET` 200；提交人工评分 `POST` 201；再次 `GET` 200，读回 1 条评分，综合分 0.74。
- API 请求使用测试密钥和模拟网关注入的 `super_admin` 身份头；没有对主库写入，也没有触发模型调用。测试代码实际载入的 Gate/feedback/auth/repository 源文件哈希与 main 工作树一致；`curator.append_case` 函数实现一致。容器 `GIT_COMMIT` 未提供，因此未将容器镜像标记为可追溯构建。
- 清理：临时 Postgres/Redis 容器及匿名卷已删除，主机临时脚本和环境文件已删除；主仓 `git status` 未新增受本任务影响的文件。

## 2026-11-05 在线 sampler 预算上下文修复（PR 待创建）

- 工作树：`agent-wt-eval-sampler-budget`，分支 `codex/eval-online-sampler-budget`，基于 `origin/main=040453a`。
- 改动：`backend/evaluation/online_sampler.py` 在后台 judge 调用外层绑定评测身份及 `scoped_request_budget`，复用既有预算预占/实际用量结算路径；enforce 模式预算绑定失败时阻断模型调用。`backend/tests/evaluation/test_online_sampler.py` 覆盖身份/租户/request_id、reserve/settle 和失败阻断；测试负反馈读取已 mock，不触碰共享业务库。
- 验证证据（本工作树执行）：定向 sampler 测试 **19 passed**、ruff check 通过、py_compile 通过、`git diff --check` 通过。`ruff format --check` 对改动文件提示需格式化，但基线同样不通过；未对全文件做大面积格式化。
- 受影响服务已从本工作树执行 `devctl.bat rebuild /y`，命令退出码 0；API、APISIX、RAG、MCP、前后端健康检查均健康，Beat 动态导入后注册 `evaluation.online_sampling`。无真实模型调用；`EVAL_ONLINE_SAMPLER_ENABLED` 仍为空/默认关闭。
- 当前状态：实现和本地验证完成，尚未 commit/push/开 PR；不能据此宣称 CI、Review、合并完成，也未实测真实预算服务结算。下一步先审阅本次 diff，提交并开独立 PR；CI 通过合并后再做隔离 APISIX/JWT + 浏览器写操作验收。
