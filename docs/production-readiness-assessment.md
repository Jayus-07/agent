# 生产就绪度（Production-Ready）评估报告

- **评估对象**：电商智能运营 Agent 平台（FastAPI + LangGraph + Next.js）
- **评估日期**：2026-08-21
- **评估范围**：backend（454 个 .py，约 6.5 万行）、frontend、docker 部署配置、测试体系
- **评估方式**：静态代码审查 + 量化 grep 统计 + 关键发现人工复核

---

## 总体结论

**当前不具备生产就绪条件。**

代码架构与工程质量**明显高于同类项目平均水平**（分层清晰、docstring 覆盖 96%、SQL 六层校验、统一日志/指标骨架），但存在 **4 个 P0 阻断项**（凭据泄漏、认证裸奔、测试不进 CI、依赖不可复现）和一批 P1 短板，集中在**安全、测试、部署运维**三个维度。按清单完成 P0 + 核心P1 后可达到准生产状态。

### 维度评分总览

| # | 维度 | 评分 | 状态 |
|---|------|:---:|------|
| 1 | 代码质量与可维护性 | 7/10 | 良好（局部技术债） |
| 2 | 错误处理与异常恢复 | 6/10 | 中等（骨架好、收尾差） |
| 3 | 日志记录与可观测性 | 6/10 | 中等（有指标、无告警外推） |
| 4 | 安全性 | 3/10 | **较差（P0 阻断）** |
| 5 | 性能与资源利用 | 7/10 | 良好 |
| 6 | 可扩展性 | 4/10 | 较差（单副本架构假设） |
| 7 | 可靠性 | 5/10 | 中等 |
| 8 | 测试覆盖率与用例质量 | 3/10 | **较差（P0 阻断）** |
| 9 | 配置管理与部署 | 4/10 | 较差 |

---

## 一、代码质量与可维护性 —— 良好（7/10）

**做得好的：**
- 分层架构严格执行（app → orchestration/agents → skills → tools → infra），grep 未发现底层反向 import 高层的违例。
- 命名规范高度一致：全库 camelCase 函数仅 1 处（logging 框架 override，合理）。
- docstring 覆盖 96%（333/346 非测试文件），且质量高（含 ASCII 架构图、ADR 引用）。
- TODO/FIXME 仅 2 处，均为规划中功能，无腐化标记。

**问题：**
| 问题 | 严重度 | 证据 |
|------|:---:|------|
| `except Exception` 泛滥：非测试代码 359 处，其中约 39 处后随 `pass` 静默吞异常 | **P1** | planner.py:280/288/298、llm_router.py:55-67 连续 3 个 pass |
| `_extract_json`（LLM 输出解析）在 4 处复制粘贴且签名不一致 | P1 | planner.py:265、llm_router.py:51、nli_llm.py:66、analyzer.py:29 |
| skills ↔ orchestration 双向依赖（靠延迟 import 规避循环） | P2 | skills/registry.py:45 ↔ orchestration/tool_registry.py:70 |
| 超大文件：indexer.py 1295 行（单类 19 个方法）、rag/chain.py 939 行、rag_upload.py 841 行 | P2 | rag/indexing/indexer.py |
| 返回类型注解覆盖率约 62%；`backend/backend/` 误导性嵌套目录、`eval/` 死目录 | P2 | — |

---

## 二、错误处理与异常恢复 —— 中等（6/10）

**做得好的：**
- 全局异常处理成熟：三层 handler（server.py:72-75），500 兜底不泄露堆栈，统一 `{error, detail}` 响应格式。
- 超时控制全面：LLM request_timeout、SQL `SET LOCAL statement_timeout` + 只读事务、HTTP timeout=10/60s（全仓 42 处 timeout）。
- Skill 层有带指数退避的重试 + 可重试错误分类（skills/base.py:126-174）。
- 降级链已接入 Supervisor 主调度节点（scheduler.py:175 调用 `execute_degradation`，sql.query↔rag.search 互降、每步限降 1 次）。

**问题：**
| 问题 | 严重度 | 证据 |
|------|:---:|------|
| LLM 熔断器（CircuitBreakerOpen）在生产代码中**零捕获**，开路后直接 500，无 fallback 模型 | **P1** | infra/llm/proxy.py:232/242 |
| LLM 层无显式重试（依赖 LangChain 内部默认） | P1 | — |
| 39 处静默吞异常掩盖路由决策依据，故障排查困难 | P1 | 见维度一 |
| TraceCollector 埋点 bug 反复触发：`'TraceCollector' object has no attribute 'start_span'`，导致告警记录全是 WORKER_RETRY_EXHAUST | P1 | logs/degradation.jsonl |
| Windows 下超时只 join 不中断线程，超时后线程仍占资源 | P2 | infra/timeout.py:32-60 |
| RAG 上传等写路径无事务补偿，仅靠启动时清理孤儿文件"事后补救" | P2 | server.py:120-136 |

---

## 三、日志记录与可观测性 —— 中等（6/10）

**做得好的：**
- 生产代码 `print(` 残留 **0 处**；统一 logger（335 处）。
- 结构化 JSON 日志（LOG_FORMAT=json）+ trace_id/session_id 经 contextvar 自动注入。
- Prometheus 指标已暴露 `/metrics`（LLM token、skill 失败率等）；`/health` 含 RAG 状态。

**问题：**
| 问题 | 严重度 | 证据 |
|------|:---:|------|
| 告警只追加写本地 JSONL 文件，**无 Alertmanager/webhook/邮件等任何外推通道**，metrics 也无告警规则 | **P1** | observability/alerts.py:52-60 |
| 无 OpenTelemetry，自研 tracer 无法对接 Jaeger/Tempo 生态 | P2 | 全仓 0 处 OTel |
| 文件 handler 只记 WARNING 及以上，大量 INFO 排障信息不落盘；默认格式为 text 需显式切 JSON | P2 | shared/logger.py:180 |
| liveness 与 readiness 未分离 | P2 | app/api/routes/health.py |

---

## 四、安全性 —— 较差（3/10，P0 阻断）

**做得好的：**
- SQL 注入防护质量高：sqlglot AST 六层校验（单条 SELECT / 表白名单 / 敏感列拒绝 / 禁函数 / 强制 LIMIT / 只读角色），参数化查询为主。
- CORS 从环境变量读取（默认 localhost:3000）、上传 413 拦截、双层 Token Bucket 限流 + 并发信号量。

**问题：**
| 问题 | 严重度 | 证据 |
|------|:---:|------|
| **真实 PG 密码硬编码在 git 跟踪文件中**：`PGPASSWORD=9t792LgUaqL1VWfTRYjeSJd1` | **P0** | backend/.env.example:6（已人工复核确认） |
| **认证可静默失效**：未配置 API_KEY 时全部端点放行，仅 warning；且运行日志显示当前环境正是无认证状态（"API_KEY 未配置！所有 API 端点公开可访问"，2026-08-21 18:15） | **P0** | app/api/middleware/auth.py:36-39（已复核） |
| web_crawl 对 URL 零校验：无 scheme 白名单、无私网 IP/云元数据地址（169.254.169.254）拦截，LLM 生成的 URL 可探测内网（SSRF） | **P1** | tools/web.py:88-113 |
| 行级权限/敏感列/脱敏机制已实现但**配置全空未启用**；`current_user_id` 由客户端自报可伪造 | P1 | sql/data/schema_config.py:262-268 |
| API Key 明文比较（非常量时间），管理端点与业务端点共用同一 Key，无权限分级、无用户体系 | P1 | auth.py:43 |
| 限流器 `_users` 字典无淘汰机制，可被海量伪造 user_id 撑大内存 | P2 | infra/llm/rate_limiter.py |

---

## 五、性能与资源利用 —— 良好（7/10）

**做得好的：**
- 异步模型正确：路由全 async，重操作统一 `asyncio.to_thread`（15 处），SSE 专用线程池。
- 连接池规范：getconn/putconn 严格配对 + 归还前 rollback + keepalives；未见泄漏路径。
- 上传 8MB 分块流式写盘、SQL 结果强制 LIMIT 100、crawl 内容截断。

**问题：**
| 问题 | 严重度 | 证据 |
|------|:---:|------|
| `/llm/balance` 在 async 函数内同步 `requests.get`，阻塞事件循环 | P1 | infra/llm/providers/deepseek.py:48 |
| 无 embedding 缓存、无查询结果缓存——相同问题重复检索/重嵌入 | P2 | — |
| `execute_sql` 用 `cur.fetchall()` 全量加载（受 LIMIT 保护，风险可控） | P2 | sql/executor.py:220 |

---

## 六、可扩展性 —— 较差（4/10）

| 问题 | 严重度 | 证据 |
|------|:---:|------|
| 大量进程内状态阻碍多副本部署：SSE 中止信号 `_active_stops: dict`、进度队列 `_progress_queues: dict` 均为内存态，跨副本失效 | **P1** | chat.py:43、_rag_shared.py:19 |
| WorkflowScheduler 定时任务在进程内启动（server.py:330-352），多副本会重复触发日报/周评测 | **P1** | — |
| 文档存储依赖本地磁盘（DOCS_DIRECTORY + SQLite），无共享存储假设 | P1 | config/database.py:29-32 |
| 限流器、模型切换工厂为进程级单例，多副本状态不一致 | P1 | — |
| **实质为单副本架构**：并发能力受限于单进程（uvicorn workers=1 假设），水平扩展需先完成状态外置 | P1 | 综合 |

---

## 七、可靠性 —— 中等（5/10）

**做得好的：** 分层重试（Skill 层退避重试）、SQL 只读事务 + 错误分类、降级链真实接入 Supervisor、启动时清理 stale lock/孤儿文件。

**问题：**
| 问题 | 严重度 |
|------|:---:|
| 熔断开路无 fallback LLM 路径，LLM 故障 = 服务不可用 | **P1** |
| 告警无通知通道，故障发现依赖人肉 grep 日志（MTTR 不可控） | **P1** |
| 数据库无迁移版本管理（见维度九），升级存量库无安全手段 | **P1** |
| 埋点 bug 导致降级/重试记录失真 | P1 |

---

## 八、测试覆盖率与用例质量 —— 较差（3/10，P0 阻断）

**做得好的：** 测试资产数量可观（backend/tests 96 文件、1017 个用例；根目录 tests/ 另有 4 文件）；抽查 orchestration/workflow 套件为高质量单测（fixture + AsyncMock，覆盖率≈100%）。

**问题：**
| 问题 | 严重度 | 证据 |
|------|:---:|------|
| **1017 个单测不在任何 CI 中运行**：.github/workflows 仅有 rag_eval.yml（RAG 评测），无 unit-test workflow | **P0** | 已复核确认 |
| **核心链路零覆盖**：覆盖率报告显示 app/api（1829 语句）、agents/planner、agents/reporter、skills/、memory/ 均 0%；业务代码总覆盖约 6.6% | **P0** | htmlcov/status.json |
| 覆盖率门禁为 0（`--cov-fail-under=0`），形同虚设 | P0 | pytest.ini |
| 根目录 tests/ 不在 testpaths 内，默认永不执行（含 test_chat_stream_body.py 等有价值回归测试） | P1 | pyproject.toml |
| PG 集成测试无库时静默 skip，CI 中实际被跳过；个别测试含 `assert True` 弱断言 | P2 | test_pg_integration.py、test_metrics.py |

---

## 九、配置管理与部署 —— 较差（4/10）

**做得好的：** config/ 按模块集中管理 10 个文件；.gitignore 正确排除 .env（根 .env 未入库）。

**问题：**
| 问题 | 严重度 | 证据 |
|------|:---:|------|
| **依赖全部 `>=` 浮动版本、无锁定文件**（无 requirements 锁版/uv.lock/poetry.lock），构建不可复现；CI 装的是 requirements-dev.txt，与 pyproject 双依赖源漂移 | **P0** | pyproject.toml（已复核） |
| Dockerfile：单阶段构建、root 运行、无 HEALTHCHECK、build-essential 未清理 | P1 | Dockerfile |
| docker/entrypoint.sh + init-db.sql 为死代码（Dockerfile 未引用），且模块路径错误（`python -m api.server` 应为 backend.app.server）——启用即失败 | P1 | docker/entrypoint.sh |
| compose：应用用 postgres 超级用户连库（只读角色设计未用）、PGPASSWORD 默认 postgres、5432 对外暴露 | P1 | docker-compose.yml |
| 无 pydantic Settings 启动校验（API_KEY 空串静默通过）；业务代码散落 os.getenv 21 处，违反自家 CLAUDE.md 规范 | P1 | chat.py:37-47 等 8 文件 |
| 无 alembic/版本表，迁移靠手动 rebuild_pg.py 重放（含 DROP） | P1 | scripts/rebuild_pg.py |
| start_all.bat（--reload + taskkill）纯开发用；无 K8s manifests、无生产部署文档 | P2 | — |

---

## 优化事项清单（按优先级排序）

### P0 — 上线前必须完成（安全与质量门禁）

> **状态更新（2026-08-21 21:00）：五项已全部落地**，明细见文末「P0 完成记录」。

| # | 事项 | 涉及位置 | 状态 |
|---|------|---------|------|
| 1 | **轮换泄漏的 PG 密码**，从 .env.example 移除真实值（改占位符），并用 git filter-repo 清理历史 | backend/.env.example:6 | ✅ 文件已清理；密码已实测失效（2026-08-21 验证，见完成记录）；git 历史重写为可选加固项 |
| 2 | **认证强制化**：API_KEY 未配置时 fail-closed（503 拒绝）而非警告放行；改用 `secrets.compare_digest` | app/api/middleware/auth.py | ✅ 已完成，含 7 个回归测试 |
| 3 | **新增 unit-test CI workflow**：运行 pytest，根目录 tests/ 已并入 testpaths | .github/workflows/unit-tests.yml | ✅ 已完成，含凭据防回归检查 |
| 4 | **依赖锁定**：生成 requirements-lock.txt（327 个固定版本），CI 统一从锁文件安装 | requirements-lock.txt | ✅ 已完成 |
| 5 | 覆盖率门禁从 0 提到 ≥40%（实测基线 61%，门禁设 55%） | pytest.ini | ✅ 已完成 |

#### P0 完成记录（2026-08-21）

1. **凭据清理**：`backend/.env.example` 真实密码已替换为 `change-me-please` 占位符，新增 `API_KEY` / `ALLOW_UNAUTHENTICATED` 配置说明；全仓 grep 确认该密码已不存在于工作区。**密码轮换已确认完成（2026-08-21 21:40 实证）**：经用户说明，该密码此前已通过 agent 重置生成新密码；实测泄漏密码连接本地 PG（agent_memory 库）认证失败、当前 `.env` 密码连接成功，证明泄漏值已失效，风险解除。**可选加固项**：git 历史重写 `git filter-repo --replace-text <(echo '<旧密码>==>***')` + 强推 gitee 远端（会改写全部 commit hash，需团队协调）——因密码已失效，此项仅为消除历史敏感信息的纵深防御，非必须。
2. **认证 fail-closed**：中间件重写——未配置 API_KEY 且未显式设置 `ALLOW_UNAUTHENTICATED=true` 时，业务端点一律 503（`AuthNotConfigured`）；Key 比较改用 `secrets.compare_digest`（utf-8 编码，兼容非 ASCII）；`/health`、`/metrics`、`/docs` 保持免认证。本地开发通过根 `.env` 的 `ALLOW_UNAUTHENTICATED=true` 显式豁免（前端暂不携带 Key）；docker-compose 已透传 `API_KEY`。新增 `backend/tests/test_auth_middleware.py`（7 用例全通过）。
3. **单测 CI**：新增 `.github/workflows/unit-tests.yml`——push/PR 触发、Python 3.10、从 `requirements-lock.txt` 安装、跑全量 pytest、上传 htmlcov 产物；附带「.env.example 疑似真实凭据」防回归检查步骤。`pytest.ini`/`pyproject.toml` 的 testpaths 均已并入根目录 `tests/`（24 用例，原先永不执行）。
4. **依赖锁定**：`requirements-lock.txt` 由当前验证可运行的 .venv `pip freeze --all` 生成（327 包固定版本，含 torch 2.11.0），文件头注明生成与升级流程；CI 不再使用 requirements-dev.txt 双源安装。
5. **覆盖率门禁**：全量实测（1078 通过 / 3 跳过 / 1 既有失败）总覆盖率 **61%**——旧 htmlcov 报告的 11.8% 为部分运行的产物；门禁设为 `--cov-fail-under=55`。遗留：`test_rag_upload_mime.py` 1 个失败为 CSV 支持 WIP（PARSABLE_EXTS 已含 .csv 而 ALLOWED_MIME_TYPES 未同步），待 CSV 功能收口时修复。

### P1 — 上线后第一个迭代内完成

| # | 事项 | 涉及位置 |
|---|------|---------|
| 6 | web_crawl 增加 SSRF 防护：scheme 白名单 + 解析 DNS 后拦截私网/环回/元数据 IP | tools/web.py:88-113 |
| 7 | 为 CircuitBreakerOpen 增加 fallback（备用 LLM 或降级到缓存/拒答话术）；LLM 调用加显式重试 | infra/llm/proxy.py |
| 8 | 告警外推：alerts.py 接入 webhook（钉钉/企微/邮件），配置 Prometheus 告警规则 | observability/alerts.py |
| 9 | 修复 TraceCollector start_span 埋点 bug（当前降级/重试观测数据失真） | observability/tracer.py |
| 10 | 收敛 359 处 except Exception：区分可恢复/不可恢复，吞异常处至少补 warning 日志 | 全局，优先 planner/llm_router |
| 11 | 启用行级安全/敏感列/脱敏配置；current_user_id 改为服务端会话推导 | sql/data/schema_config.py |
| 12 | Docker 生产化：非 root 用户 + HEALTHCHECK + 多阶段构建；修复或删除 entrypoint.sh 死代码；compose 改用只读账号、PG 不对外暴露端口 | Dockerfile、docker/ |
| 13 | 引入 alembic 迁移版本管理，替代手动 rebuild | backend/sql/migrations |
| 14 | 统一 `_extract_json` 到 shared；引入 pydantic Settings 启动校验；清理 21 处散落 os.getenv | 4 处复制 / config/ |
| 15 | 多副本准备（若需水平扩展）：SSE 中止信号/进度队列外置 Redis、定时任务加分布式锁、文档存储上对象存储/共享卷 | chat.py、server.py:330 |
| 16 | `/llm/balance` 改 async httpx | deepseek.py:48 |

### P2 — 技术债，随迭代偿还

- 拆分 indexer.py（1295 行）、rag/chain.py、rag_upload.py（业务下沉 service 层）
- 解耦 skills ↔ orchestration 双向依赖（注册中心下沉独立层）
- 清理 backend/backend/、eval/ 死目录；返回类型注解从 62% 提升至 90%
- 日志文件级别 INFO、默认 JSON 格式；liveness/readiness 分离；评估引入 OpenTelemetry
- 限流用户桶加 LRU 淘汰；增加 embedding/查询结果缓存
- 补生产部署文档（docs/operations/ 扩展）

---

## 结论

该平台的**架构设计与代码卫生属于优秀水平**（分层边界、SQL 安全链路、异步工程化都经过多轮加固），主要差距不在"写得好不好"，而在**"运维闭环是否形成"**：凭据管理、认证强制、CI 门禁、告警通知、可复现部署这五件事是 demo 工程与生产工程的分水岭。完成 P0 五项（约 2-3 人日）后可安全地进行内部试运行；P1 十一项完成后具备对外生产条件。
