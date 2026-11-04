# PROGRESS: 评测闭环收口（eval-closure）

- 状态：**IN_PROGRESS**（E'、D1、D3、D4a 已收口；F 代码已合并但 F1-F3 写操作实机验收 **UNVERIFIED**）
- 当前工作树：`agent-wt-eval-d4a` / `fix/deps-d4a-cross-major`；F 与 D3 worktree 保留，不清理。
- 当前基线：`origin/main=073c613`；任务沿革基线见 TASK.md。

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
- [x] F1 RunsTab：Gate 状态/审批/来源徽标（PR #328；写操作 API 实测 UNVERIFIED）
- [x] F2 DatasetsTab：反馈候选审核（PR #328；写操作 API 实测 UNVERIFIED）
- [x] F3 CompareTab：人工评审挂载（PR #328；提交 API 实测 UNVERIFIED）

## 阻塞

- F1-F3 代码已合并。写操作实机验证缺安全测试数据：V2 没有 PASS 运行，feedback 候选为 0；不对共享库造数或审批现有失败运行。

## 下一步

1. F1-F3 写操作实机闭环仍 UNVERIFIED：待有隔离的 PASS run、feedback candidate 和绑定 Human Rubric 的用例后再执行；
2. 不在当前共享业务数据上造数或审批现有失败运行；
3. ECS 三项与 pillow/filelock/dnspython/unstructured 按既有裁决保持不动。

## 2026-11-05 接手批续工（F / D3）

### F 批（管理端）

- PR [#328](https://github.com/Jayus-07/agent/pull/328) 已于 2026-11-05 合并，HEAD `27320474a0d53e766e2b7a7f93fb525a5a11320f`，merge commit `83aa74ca81a2c91774d084dfd41e80d059fe90a2`。
- 改动：RunsTab Gate 状态/审批/线上来源、DatasetsTab feedback 候选审核与晋升、CompareTab 人工评审挂载；新增三处组件测试。
- 定向测试 4 个组件文件 / 6 个测试通过；`tsc --noEmit` 通过；任务 worktree `devctl.bat rebuild /y` 完成，API / 管理端健康检查 200。
- super_admin 只读 API 实测运行和反馈候选 GET 均 200，logout 200。写操作实机验收 **UNVERIFIED**：本地 V2 运行 8 条均为 `FAIL/INVALID`、无 PASS；`agent_business.public.feedback_candidates` 为 0 行。没有伪造记录或改写共享库。TASK.md 的 F1-F3 实机写操作验收保持未勾选。

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
