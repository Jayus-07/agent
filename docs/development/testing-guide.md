# 测试策略

本文件是唯一详细测试策略。每次验证依据变更风险、依赖范围和失败影响选择；不按修改行数机械分级。

## 分级

| 级别 | 范围 | 适用情况 |
| --- | --- | --- |
| T0 | 格式、类型、静态检查、文档链接和契约生成器检查 | 文档修改或不执行运行逻辑的轻量改动 |
| T1 | 受影响函数、组件或模块的定向测试 | 单一模块的行为变化或 Bug 修复 |
| T2 | 关键调用方、接口、SSE、权限、状态和公共契约测试 | 跨模块改动、共享组件或安全相关变化 |
| T3 | 全量或发布级回归及外部链路验收 | 用户明确要求、发布门禁或影响分析确认必要时 |

## 选择规则

- 默认采用最小充分集合；安全、身份、权限、租户、状态迁移、Tool 治理和公共契约按其风险升到 T2 或更高。
- Bug 修复覆盖原失败条件和关键失败路径。只验证 import 成功、HTTP 200 或“未抛异常”不足以证明行为正确。
- 不默认运行全量 pytest、E2E、真实 LLM 评测、数据库迁移或索引重建。
- 共享 PostgreSQL、Redis、索引或数据集的测试不得在不确认隔离条件时并行运行。
- 测试失败先判断是否由本次改动导致，再运行失败用例和必要关联范围。
- 不能执行时注明环境、凭据或 Provider 限制，并标记为未验证或 BLOCKED。
- 不通过删断言、批量 skip/xfail、扩大 mock 或降低验收条件提速。
- 只有旧契约确实废弃且替代覆盖已建立，才删除原测试。

## 常用运行形式

新增 `backend/tests` 顶层目录必须同步 `full_regression.yml` matrix；目录覆盖守卫会阻止漏登记（排除 fixtures 与 __pycache__）。

后端定向测试从仓库根目录使用项目 Python，并显式给出测试路径：

~~~bash
python -m pytest backend/tests/<受影响路径> -q --no-cov
~~~

前端使用对应应用的 Vitest 脚本，并明确测试文件；类型变化可另运行 TypeScript 检查。按需读取该应用的 package.json 确认脚本。

## CI 诊断资产与 Redis 测试库约定

### PYTEST_XDIAG=1 诊断三件套（xdist 停摆定位）

背景：全量回归 xdist（4 worker）出现过三类无现场停摆——worker 进程消失（xdist 报 `node down: Not properly terminated`）、150min job 被杀且 pytest 输出 0 字节、收集期（collection/import）卡死。pytest-timeout 的 thread 法对卡在 C 层/锁上的用例无法中断，watchdog 兜底 `os._exit` 时整进程消失、栈写不进管道，主进程只见通道 EOF。三类症状此前全部无现场。

开关 `PYTEST_XDIAG=1`（CI [full_regression.yml](../../.github/workflows/full_regression.yml) 已常驻开启；实现见 [backend/tests/conftest.py](../../backend/tests/conftest.py) 的 xdist worker 诊断段），三个机制全部写独立文件、不依赖 stdout：

| 机制 | 覆盖的停摆 | 产出 |
| --- | --- | --- |
| patch pytest_timeout watchdog：`os._exit`/fail 前用 faulthandler 同步落全线程栈（C 层写，`os._exit` 前必然写完） | 用例超时被兜底杀进程 | `diag_<worker>.stacks` 中 `===== pytest-timeout watchdog 触发 =====` 标记行 + 全线程栈 |
| faulthandler.dump_traceback_later 每 300s 一拍心跳（原生 C watchdog，不需要 GIL） | GIL 死锁、收集期卡死等 pytest-timeout 够不着的停摆 | 连续多拍不变的栈快照即卡点（配合打点：无新 START + 栈不变） |
| 用例级 START/DONE 打点（logstart/logfinish） | crash 后定位正在跑的用例 | `diag_<worker>.tests` 尾部有 START 无 DONE 的用例 |

产出目录由 `PYTEST_XDIAG_DIR` 控制（CI 为 `xdiag/`，本地缺省 `tmp_xdiag/`）；每个 xdist worker（`gw0`–`gw3`）各一对文件，非 xdist 主进程 tag 为 `main<pid>`。CI 侧随 `failures-<suite>` artifact 一并上传；本地复现挂死时同样带开关跑：

~~~bash
PYTEST_XDIAG=1 PYTEST_XDIAG_DIR=/d/tmp/xdiag-local python -m pytest backend/tests/<路径> -q --no-cov --timeout=150
~~~

判读顺序：先看 `.tests` 尾部正在跑的用例，再看 `.stacks` 最后几拍栈是否变化；watchdog 标记行之后紧随的是被杀瞬间的全线程栈。

### Redis 专用测试库的 worker 分片（flushdb 纪律）

背景事故：v19–v22 轮 CI `test_heartbeat_renews_admission_token` 恒红——xdist 4 worker 共享同一 Redis db，某 worker 测试里的 `flushdb` 是整库操作，把并行 worker 心跳中的 admission 键一并清掉。

约定（[backend/tests/fixtures/redis_test.py](../../backend/tests/fixtures/redis_test.py)）：

- 测试内的 `flushdb` 必须先经 `redis_test_db_url(REDIS_URL)` 换算出本 worker 专用 db 号（`15 - gw 序号`，Redis 默认 16 库不越界），再对该连接整库清空；既有入口参考 `test_stop_g4_matrix.py`、`test_task_admission*.py`。
- 禁止裸连固定 db（含 db15）后 flush；读写普通测试键不受限，只有「整库清空」必须走分片 db。
- 连接 host 钉死 `127.0.0.1`：`localhost` 会在 `::1`/`127.0.0.1` 间轮换解析，偶发连到不同端点。

## 交付记录

报告实际命令、通过/失败/跳过数量、耗时、未运行范围及原因。T0/T1/T2 的局部通过不等于全量或生产验收通过。
## 失败与降级的回归矩阵

涉及 Tool、Provider、MCP、Agent 交接或 Reporter 时，在受影响链路上覆盖 [失败规范 F-01～F-12](../architecture/failure-degradation.md)，优先验证**用户可感知终态**而不只是底层函数返回：

| 改动 | 最小足够的测试（按需要升级） |
| --- | --- |
| Tool adapter / Schema | T1：HTTP 200 但业务 failed、empty、非法格式、成功数据；T2：Skill/Workflow 消费边界 |
| SafeToolExecutor / Tool Governance | T2：连接/读超时、重试预算、熔断/舱满、鉴权/租户、写操作禁止重投；共享调用方 |
| Domain Agent / Supervisor / Reporter | T1+T2：普通请求允许 DEGRADED；同一工具遇到用户硬约束 BLOCKED；内部异常 FAILED；前端/SSE 与终态一致 |
| 退款/预订/其它副作用 | T2：用户确认、幂等键、结果未知 IN_DOUBT、重复提交不重复生效、跨租户拒绝 |
| Prompt/Router/LLM 路由选择 | T2：候选白名单、低置信度澄清、业务失败不被模型生成成功；必要时隔离集成测试 |

推荐既有入口：`backend/tests/tool_runtime/test_governance.py`、`backend/tests/travel/test_tool_failure_policy.py`、`backend/tests/skills/test_tool_contract_boundary.py`。先检查项目实际测试列表及 fixture，路径不是覆盖已足够的证明。生产真实 Provider、端到端浏览器以及真实 LLM 评测仅在依赖就绪且风险需要时运行。

涉及目录迁移、跨入口改动或发布时，按 [安全重构手册](refactoring-playbook.md) 核对静态/动态 import 与兼容引用，按 [发布质量门禁](../operations/release-quality.md) 补齐实际执行证据。

## 旧测试清理与测试证据

旧测试过期时先建立**旧契约 → 当前事实源 → 替代测试 → 覆盖证据**的映射：确认功能/接口已废弃，替代用例能捕捉关键回归，再删除或迁移原用例；不要只因慢、红或重复就删掉。按风险拆分快慢测试集；T1 先跑单模块，T2 扩展跨层消费者，T3 仅发布门禁/明确要求触发。

验收记录至少包括：Git commit/ref、测试命令、用例范围、PASS/FAIL/SKIP、耗时、缺少的服务/凭据、是否真实 Provider、相关 Trace 或 API 响应证据。不能从一条 HTTP 200 或一次定向 pytest PASS 推导全链路生产就绪。

## AI 质量评测与工程测试的分工

T0–T3 负责**工程风险选择**；Golden/Suite/Runner/Live/Release Gate 负责**业务与模型质量**。完整规则、按领域指标、版本与 PASS/FAIL/INVALID 判定见 [全项目功能评测治理](../evaluation/evaluation-governance.md)。修改检索、SQL、Agent、Prompt、供应商或客服状态时必须按影响选择对应质量 Suite 和权限/异常断言；CI 工程测试通过不等于 AI 质量评测已通过。
