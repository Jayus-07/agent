# PROGRESS — RAG 生命周期审计收口

## 前置：三条线索的验证结论（2026-11-09，先验证后修）

验证方式：基于 `f0e4b22` 的独立工作树，用 `agent-app` 镜像挂载源码跑只读探针（真实 PG 注册表 + 真实路由函数），探针文件用后即删。

| 线索 | 结论 | 证据 |
|---|---|---|
| #61 下线后排队发布复活 | 成立 | `register → active→deprecated（rowcount=1）→ register_published(expected_base='')` 返回 rowcount=1，最终 status=active、generation=g2 |
| #62 非 active 文档 chunk 正文可读 | 成立 | `deprecated`/`pending_review` 时 detail 返回 total=1 且含正文，兄弟端点 chunks 同状态返回 0 |
| #65 撤权后 worker 仍发布 | 源码层成立 | `backend/tasks/index_tasks.py` 全链无 `resolve_task_authorization`；发布前唯一门是质量门 |

## 实施

| 文件 | 改动 |
|---|---|
| backend/rag/indexing/doc_registry_pg.py | register_published 增加 expected_base_status 守卫（仅当当前 status 仍等于认领状态才允许覆盖） |
| backend/rag/indexing/index_run_store_pg.py | rag_index_runs 增加 base_status 列（惰性补列），create_run 固化认领状态 |
| backend/app/api/routes/rag_upload.py | 两处 create_run 传 base_status；发布段传 base_status；新增发布前授权复验 _reverify_publish_authorization |
| backend/rag/indexing/publish.py | publish_candidate 透传 base_status；新增 PublishAuthorizationRevoked（SupersededCandidate 子类） |
| backend/rag/indexing/lifecycle.py | 下线/送审成功后取消该文件在途运行（标 superseded），失败只记日志不阻断迁移 |
| backend/app/api/routes/rag_lifecycle.py | 生命周期服务注入 run store |
| backend/app/api/routes/rag_documents.py | chunk 详情增加发布状态门，管理员保留诊断 |
| .github/workflows/rag_smoke.yml | 注册 backend/tests/rag/test_lifecycle_publication_guard.py |
| backend/tests/rag/test_lifecycle_publication_guard.py | 13 条回归（PG 4 + 生命周期 2 + chunk 门 3 + 授权复验 4） |

## 验证证据

- 新增回归：13 passed（含真实 PG 注册表用例）。
- 关联回归：test_candidate_publish / test_doc_registry_pg / test_knowledge_lifecycle / test_rag_lifecycle_api / test_rag_upload_celery_mode 共 92 passed。
- 上传链路：test_rag_upload_to_index_e2e + test_rag_upload_concurrency + api lifecycle + knowledge lifecycle 共 46 passed。
- 活体授权口径核对：resolve_task_authorization → RagAuthorization 对真实用户行重建，editor/admin 在目标 KB/部门可通过；确认 HTTP 部门来自请求头，故复验用运行记录部门（已写入代码注释与用例）。
- 本机重建：devctl.bat rebuild -y 成功，/health 200、迁移与 schema 一致性 ok。

## 文档同步（现行规范，非任务流水账）

| 文档 | 增补内容 |
|---|---|
| docs/domains/rag.md | 「生命周期与发布不变量」：发布是唯一可见性切换点、撤销优先于排队发布、正文三面同口径、发布前复验授权；链回对应测试 |
| docs/architecture/async-task-governance.md | 不变量新增「身份固定、授权动态」；AT-06 验收补执行期复验 |
| docs/development/testing-guide.md | 回归矩阵新增 RAG 生命周期/发布与异步任务撤权两行；新增「测试文件 docstring + 现行文档双向索引」约定 |

任务文档（本目录 TASK/PROGRESS）只记录目标、决策与证据，不作为规范事实源。

## 待办

- 本地重建与健康验证。
- PR 与 CI。
- 生产发布（ECS 访问受阻，见交接记录）。
