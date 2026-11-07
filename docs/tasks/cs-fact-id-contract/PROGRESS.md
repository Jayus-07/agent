# 进度

## 状态

`IN_PROGRESS`。本任务只做离线和代码验证，不调用真实模型或 Provider。

## 基线和工作区

- Worktree：`C:\Users\wh\.codex\worktrees\enterprise-agent-cs-facts\agent`
- Branch：`codex/cs-fact-id-contract`
- Base HEAD：`baffc319bfe6bcbb60aec64aca99891feccf6d0e`（PR #349 merge）
- Docker Desktop Linux Engine 当前不可用；部署重建暂不能验收。

## 已核实事实

- `cs_eval_kb` 在 KB registry 标记为 `audience=test`，客服生产知识链使用 `subject_type="customer"`，不得为评测放宽客户授权。
- Suite loader 已将 `kb_id`、`fixture_set`、`dataset_version` 和 suite 身份放入 case metadata，但客服 full_path runner 仍固定传 `kb_id="default"`。
- 客服知识服务会先返回生产 FAQ 命中；评测上下文必须绕过该快捷回答，才会检索测试语料。
- RAG source 含 `filename`、`section`、`doc_id`、`version_id`，不含 KB ID。不能用通用 `doc_id` 当事实 ID，需用测试 fixture manifest 的稳定文档 ID和来源元数据严格映射。
- QueryExpert 已携带订单结构化 `service_facts` / task result。只有来源标记为沙箱且返回字段确实存在时，才可生成 demo order fact ID；business service 不得归类为 demo。
- `docs/customer-service/demo-kb` 含合成客服知识；当前尚无独立 `cs_eval_kb` fixture manifest 或专用索引入口。生产 seeder 指向 `docs/customer-service/kb`，不能复用为测试库 seeder。

## 已完成

- 从最新 `origin/main` 建立独立 Worktree 和任务分支。
- 基线为 PR #349 merge commit `baffc319bfe6bcbb60aec64aca99891feccf6d0e`；本任务独立分支当前尚未提交。
- 完成任务边界和验收条件；范围限定在登记 suite 的 full_path、test-only 客服 KB 和稳定事实来源，不改变普通客服授权或业务行为。
- 新增 `cs_eval_kb` baseline fixture manifest：15 个合成 Markdown 文档有固定 fixture doc ID、内容 SHA-256、RAG version；事实 ID 只映射到登记文档的精确章节。Catalog 校验源目录、hash、标题、ID 与路径。
- 新增显式 fixture 导入器：必须指定 `--fixture-set baseline --apply`，导入前验证 KB `audience=test`，索引与 stale cleanup 限定在 `cs_eval_kb`；该命令会调用 embedding Provider，本轮没有运行。
- 新增服务器内部 ContextVar scope，限制 KB、fixture、suite 和数据集版本；full_path runner 每轮安装/恢复 scope，但主图仍传 `kb_id="default"`。客服知识检索强制显式单 test KB、内部评测角色、关闭记忆写入并绕过生产 FAQ；scope 外普通客服仍按 customer 身份和既有 FAQ 行为运行。
- Knowledge Expert 仅在评测 scope 中用稳定文档 ID、文件名、version 和精确章节输出 KB `fact_ids`；Query Expert 仅从 `sandbox_order_service` 的 DEMO 订单结构化字段生成 DB `fact_ids`。观察器收集前序 task result，runner 在 case 内跨轮汇总，不跨 case。
- 新增契约与隔离测试并加入 `.github/workflows/rag_smoke.yml` 的免费 PR 定向清单；更新 CS 数据集说明及 CI/评测环境文档，明确代码接线不等于已导入或 Live 通过。
- 本地重跑最终定向集合：99 项测试通过；Ruff、`py_compile`、manifest JSON 与 `git diff --check` 均已验证通过，细节见下方验证记录。

## 待完成

1. 复核完整 Diff、工作区范围和 Docker 状态。
2. 提交任务分支、创建 PR，跟进自动 Review 与免费 PR CI；修正新增意见后按最新 Diff 复核并合并，记录 PR、head 和 merge SHA。
3. Docker rebuild 与服务健康检查当前 `UNVERIFIED`；Docker Engine 可用、任务环境齐备且共享栈归属安全确认后，才可按仓库部署规则定向重建并检查 `/health.build`。

## 阻塞与剩余风险

- Docker Desktop Linux Engine 当前不可用且该 Worktree 无 `.env`，无法重建受影响服务或做健康检查；不得报告为已部署。
- 用户要求本阶段仅离线与代码验证。fixture 导入需要 embedding Provider，未执行；`cs_eval_kb` 实际索引/检索及真实模型质量未验证。概念级 `forbidden_facts` Judge 明确不在本任务范围。
- pytest 结束时有已知 `TaskService.ensure_schema` PostgreSQL pool teardown timeout；本轮退出码为 0、98 项断言通过。需区分测试结果与环境 teardown 日志，该连接配置问题未在本任务中修改。

## 本地验证记录

- 定向 pytest：`99 passed in 49.19s`，退出码 0。测试结束日志显示 `TaskService.ensure_schema` 因本机 PostgreSQL `127.0.0.1:5432` 未提供密码出现 PoolTimeout；没有失败或跳过测试。
- Ruff：本任务涉及的 9 个 Python 文件 `All checks passed!`。
- `py_compile`：本任务涉及的 Python 文件全部通过。
- Fixture manifest：`python -m json.tool` 解析通过；catalog 测试校验 15 份来源文件 SHA-256 和章节映射。
- `git diff --check`：通过。
- 未调用真实模型/Provider，未执行 fixture 导入器；真实检索、Live 质量及 Docker 部署/健康状态未验收。
- PR、Review、CI 与合并尚未完成。
