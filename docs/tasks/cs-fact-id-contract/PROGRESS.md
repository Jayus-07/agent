# 进度

## 状态

`COMPLETE`（代码与离线契约验收范围）。本任务未调用真实模型或 Provider；fixture 实际导入/检索和 Docker 部署健康仍未验收，列入企业总任务的后续工作。

## 基线和工作区

- Worktree：`C:\Users\wh\.codex\worktrees\enterprise-agent-cs-facts\agent`
- Branch：`codex/cs-fact-id-contract`
- Base HEAD：`baffc319bfe6bcbb60aec64aca99891feccf6d0e`（PR #349 merge）
- Docker Desktop Linux Engine 当前不可用；部署重建暂不能验收。

## 基线阶段观察（PR 改动前）

- `cs_eval_kb` 在 KB registry 标记为 `audience=test`，客服生产知识链使用 `subject_type="customer"`，不得为评测放宽客户授权。
- Suite loader 已将 `kb_id`、`fixture_set`、`dataset_version` 和 suite 身份放入 case metadata；初始实现中的客服 full_path runner 固定传 `kb_id="default"`，本 PR 已改为从登记 metadata 建立 test KB scope。
- 客服知识服务会先返回生产 FAQ 命中；评测上下文必须绕过该快捷回答，才会检索测试语料。
- RAG source 含 `filename`、`section`、`doc_id`、`version_id`，不含 KB ID。不能用通用 `doc_id` 当事实 ID，需用测试 fixture manifest 的稳定文档 ID和来源元数据严格映射。
- QueryExpert 已携带订单结构化 `service_facts` / task result。只有来源标记为沙箱且返回字段确实存在时，才可生成 demo order fact ID；business service 不得归类为 demo。
- `docs/customer-service/demo-kb` 含合成客服知识；基线尚无独立 `cs_eval_kb` fixture manifest 或专用索引入口。当前 PR 已增加 manifest 与显式导入器；生产 seeder 仍指向 `docs/customer-service/kb`，不复用为测试库 seeder。

## 已完成

- 从最新 `origin/main` 建立独立 Worktree 和任务分支。
- 基线为 PR #349 merge commit `baffc319bfe6bcbb60aec64aca99891feccf6d0e`；PR #350 head `6e776e057668f5b6e00768f13cbe35003d0e47ff` 已 squash merge 为 `6240f556096a92969c3b86f657902bf4ebaeaa93`。
- 完成任务边界和验收条件；范围限定在登记 suite 的 full_path、test-only 客服 KB 和稳定事实来源，不改变普通客服授权或业务行为。
- 新增 `cs_eval_kb` baseline fixture manifest：15 个合成 Markdown 文档有固定 fixture doc ID、内容 SHA-256、RAG version；事实 ID 只映射到登记文档的精确章节。Catalog 校验源目录、hash、标题、ID 与路径。
- 新增显式 fixture 导入器：必须指定 `--fixture-set baseline --apply`，导入前验证 KB `audience=test`，索引与 stale cleanup 限定在 `cs_eval_kb`；该命令会调用 embedding Provider，本轮没有运行。
- 新增服务器内部 ContextVar scope，限制 KB、fixture、suite 和数据集版本；主图请求参数仍为 `kb_id="default"`，但客服知识服务在评测 scope 中强制覆写为唯一 test KB。客服知识检索使用内部评测角色、关闭记忆写入并绕过生产 FAQ；scope 外普通客服仍按 customer 身份和既有 FAQ 行为运行。
- RAG `CitationFormatter` 从回答实际引用的已验证 chunk 提取精简 citation evidence；即使同一文件引用多个章节也分别保留。该字段经本地/远端 `AskOutcome` 传入客服 Knowledge Expert，且从公开 `answer_meta` 分离；评测 scope 只从这组证据生成 KB `fact_ids`。Query Expert 仅从 `sandbox_order_service` 的 DEMO 订单结构化字段生成 DB `fact_ids`。观察器收集前序 task result，runner 在 case 内跨轮汇总，不跨 case。
- 标准后端镜像复制 `docs/customer-service/demo-kb`，让镜像内 full_path manifest 校验和显式导入器都能访问源文件；复制不触发索引或生产 KB 导入。只有 `kb_id`、`fixture_set`、`suite` 身份字段会启用 test KB scope，普通数据集仅携带 `dataset_version` 时保持兼容。
- 新增契约与隔离测试并加入 `.github/workflows/rag_smoke.yml` 的免费 PR 定向清单；更新 CS 数据集说明及 CI/评测环境文档，明确代码接线不等于已导入或 Live 通过。
- 自动 Review 的 3 个 P2 已修复、提交并合并：非 suite 仅带 `dataset_version` 时保持兼容；后端镜像复制 fixture 源目录；RAG 引用证据保留同文件多章节且评测不受普通来源卡片五条截断影响。回归覆盖同文件多章节和 6 个引用来源；三条 Review 线程已解决。
- 修复后的最终定向集合：140 项测试通过；Ruff、`py_compile`、fixture JSON、RAG Smoke YAML 与 `git diff --check` 通过，详见下方记录。

## 未验收边界与解决办法

- Docker rebuild 与服务健康检查当前 `UNVERIFIED`：本轮 `docker info` 无法连接 Docker Desktop Linux Engine named pipe。待 Docker 可用、任务环境齐备且共享栈归属安全确认后，按仓库规则定向重建并检查 `/health.build`。
- fixture 导入会调用 embedding Provider；按当前离线约束未运行，因此 `cs_eval_kb` 实际索引/检索和 Live 质量仍未验证。待用户允许进入付费阶段并建立隔离凭据、Run ID 归因后再执行。

## 阻塞与剩余风险

- Docker Desktop Linux Engine 当前不可用且该 Worktree 无 `.env`，无法重建受影响服务或做健康检查；不得报告为已部署。
- 用户要求本阶段仅离线与代码验证。fixture 导入需要 embedding Provider，未执行；`cs_eval_kb` 实际索引/检索及真实模型质量未验证。概念级 `forbidden_facts` Judge 明确不在本任务范围。
- pytest 结束时有已知 `TaskService.ensure_schema` PostgreSQL pool teardown timeout；最终定向集合退出码为 0、140 项断言通过。需区分测试结果与环境 teardown 日志，该连接配置问题未在本任务中修改。

## 本地验证记录

- 最终定向 pytest：`140 passed in 50.56s`，退出码 0，包含 RAG 本地/远端证据透传与客服 full_path 回归。pytest 收尾时 `TaskService.ensure_schema` 因本机 PostgreSQL `127.0.0.1:5432` 未提供密码出现 PoolTimeout；未失败或跳过测试。
- Ruff：本次新增/整洁模块通过；存在历史 lint 的 RAG/测试模块按既有基线规则排除 `I001,F401,E402,F841,N811,W605,E702,F821` 后通过。
- `py_compile`：全部本任务涉及的 Python 文件通过。
- Fixture manifest：`python -m json.tool` 解析通过；catalog 测试校验 15 份来源文件 SHA-256 和章节映射。
- `git diff --check`：通过。
- 未调用真实模型/Provider，未执行 fixture 导入器；真实检索、Live 质量及 Docker 部署/健康状态未验收。
- PR #350 已合并（merge SHA `6240f556096a92969c3b86f657902bf4ebaeaa93`）。合并前该 head 的 7 项检查中 6 项成功，真实 API Job 按离线限制跳过；合并后 main 的 RAG Smoke real-API Job 在零执行步骤时取消，required summary 明确因 `SMOKE_RESULT=cancelled` 而失败。该状态是离线限制导致的 Live 发布门禁 `BLOCKED`，不是离线契约测试失败，也不代表 Live 通过。
