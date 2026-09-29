# Context Budget Phase 5 — 生产硬化 + 性能降本 + 最终生产验收报告

日期：2026-09-22 ｜ 结论先行：**PHASE5_PASS = true，CONTEXT_BUDGET_PRODUCTION_READY = true**
L1-L5 上下文预算管理正式 Feature Complete，不再新增 Context Phase / L6 / 新压缩算法。

## A. 配置正式化

| 配置名 | 默认值 | env override | .env.example | 需重启 |
|---|---|---|---|---|
| CONTEXT_BUDGET_ENABLED | true | ✓ | ✓ | 否（进程内读 config，改 env 需重启进程） |
| CONTEXT_L5_ENABLED | true | ✓ | ✓ | 同上 |
| CONTEXT_L4/L5_TRIGGER_RATIO | 0.80 / 0.90 | ✓ | ✓ | 同上 |
| CONTEXT_L5_TARGET_RATIO | 0.70 | ✓ | ✓ | 同上 |
| CONTEXT_L4_KEEP_RECENT_TURNS | 4 | ✓ | ✓ | 同上 |
| CONTEXT_L5_SUMMARY_TIMEOUT_SECONDS | **30**（原 20） | ✓ | ✓ | 同上 |
| CONTEXT_L5_SUMMARY_MODEL | ""（走角色控制面） | ✓ | ✓ | 同上 |
| **CONTEXT_L5_SUMMARY_MAX_TOKENS** | **512**（新增） | ✓ | ✓ | 同上 |
| **CONTEXT_L5_SUMMARY_TEMPERATURE** | **0.2**（新增） | ✓ | ✓ | 同上 |
| LLM_CONTEXT_LENGTH / OUTPUT/SAFETY_RESERVE / L1-L3 预算 | 沿用 Phase 3 | ✓ | ✓ | 同上 |

- 全部在 `backend/config/memory.py` 有正式定义并有代码默认值，**新机器不配 .env 可正常启动**（`test_all_configs_exported_from_backend_config` 守卫）。
- 修复 Phase 4 隐患：`CONTEXT_L5_SUMMARY_MODEL` 一直未进 `config/__init__.py` 导出清单，业务层 `_cfg` 实际读不到。
- 业务代码零 `os.getenv`（统一 `_cfg` → backend.config）。
- 模型选型正式化：新增 **context_compactor 模型角色**（`model_roles.py`，inherit main，skip 失败策略），解析链 = DB binding → config fallback → inherit main；管理端可绑定，已绑 `qwen3.8-flash`。

## B. L5 模型优化

| 项 | 旧 | 新 |
|---|---|---|
| 模型 | CONTEXT_L5_SUMMARY_MODEL=qwen3.8-flash（thinking 默认开） | **context_compactor 角色 → qwen3.8-flash（DB 绑定）** |
| thinking | 开（思考链混入输出） | **显式关闭**（qwen/siliconflow 系 `extra_body={"enable_thinking": false}`，与 llm_enrichment 同口径） |
| 输出上限 | 无（模型默认） | max_tokens=512 + temperature=0.2 |
| 选择理由 | — | 三候选实测：qwen3.8-flash 质量 1.0/1.0、稳态 ~2s、¥0.8/¥2.7 每百万（含 ¥0.1 缓存输入价，比 qwen3.7-flash 便宜）；qwen3.7-flash 同质量但更贵；doubao-seed-2.0-mini 淘汰（retention 0.8、patched 92%） |

## C. 性能 Before / After（≥3 次真实摘要）

| 指标 | Before（Phase 4 / 容器复现实测） | After（Golden 27 例实测） |
|---|---|---|
| 单次延迟 | 24.7 ~ 27.4s（Phase 4）；41.5 / 17.9 / 13.5s（Phase 5 开工复现） | **P50 2.12s / P95 3.48s**（27 例：1.33~7.18s） |
| input tokens | ~545 | ~452（avg） |
| output tokens | ~2711（含 thinking） | **~107（avg，max 236）** |
| 延迟降幅 | — | **-91%（P50 口径）** |
| token 降幅 | — | **output -96% / 总量 -83%** |
| 成本 | ≈¥0.0078/次（¥0.8/¥2.7 每百万） | **≈¥0.0007/次，-91%** |

冷启动说明：每进程首次调用该模型实例需 ~15-23s（建实例+首连，实测 18.5s→2.9s 二次调用）；生产 app/worker 常驻，仅部署后首调付费，且 30s timeout 内、超时安全回退不阻断。

## D. Timeout 校准

- 最终值 **30s**（默认值 20→30，`.env` 120→30）。
- 依据：新模型稳态 P95=3.48s；进程冷启动最坏实测 23s；30s = 冷启动最坏值 × 1.3 ≈ 稳态 P95 × 8.6，正常摘要不误超时，provider 卡死 30s 内 fallback（场景4 实证 0.001s 超时 → 立即回退）。

## E. ProtectedFacts（裸金额）

- 新增语义词+数值规则（21 词：预算/金额/成本/价格/售价/采购价/退款/退款金额/收入/销售额/利润/毛利/费用/支出/单价/总价/最高/最低/上限/下限），三重防误判：间隙禁跨货币符号（「预算 ¥5000」仍归货币规则）、数字后缀为单位/日期/百分比/万亿时豁免（「35件」「10万」「18%」「2026-10-15」不误判）、订单号/SKU/库存/错误码不在词表。
- 同语义金额**最新值优先** supersede：`预算改成 120,000` → facts 只剩 120,000（`source_message_id` 最新）。
- 误报测试 8 组应保护 / 8 组不应匹配全绿；既有货币规则（¥/$/元）不回归。
- Golden 28 例：裸金额保护 B04 ✓、金额更新 B05 ✓（must_not 旧值 100,000 通过）、大数字非金额 B07 ✓。

## F. Patched 分布（§19，Golden 27 例 scored）

| type | total | preserved_by_llm | patched | ratio |
|---|---|---|---|---|
| amount | 26 | 26 | 0 | 0.0 |
| identifier | 6 | 6 | 0 | 0.0 |
| percentage | 5 | 5 | 0 | 0.0 |
| date | 5 | 5 | 0 | 0.0 |
| error_code | 4 | 4 | 0 | 0.0 |
| other | 4 | 4 | 0 | 0.0 |

Phase 4 patched≈28% → **0%**（关 thinking 后模型原生保真显著提升；无一类异常，无需 prompt 再调）。运行时另有 `context_protected_facts_total{type,result}` 低基数指标分层落盘。

## G. Golden（28 例，宿主独立进程）

| 门 | 要求 | 实测 |
|---|---|---|
| Fact Retention | 1.0 | **1.0** |
| Protected Final Recall | 1.0 | **1.0** |
| Identifier Exact Match | ≥0.99 | **1.0** |
| Numeric Exact Match | ≥0.99 | **1.0** |
| Constraint violation | 0 | **0**（B06 初版 must_not 过严——把摘要中正确的「原值 50,000 已作废」否定表述误判为泄漏；已修正测试语义并在下文 M 说明，非迁就代码：判定「当前有效值唯一」由 B05 must_not + supersede 单测覆盖） |
| 1 例 scored 外 | — | A01 首轮 run 与 case_c_l5 并发抢模型瞬时失败，安全回退正常；单独复跑 4/4 全绿（1.0） |

## H. OOM

- 根因：完整 Golden 在 **app 容器内**跑——4GiB cgroup 限额与 FastAPI 服务（常驻 1.2GB+）共享；评测进程导入全量 backend 栈 + 每例双轨全量 prompt 与答案对象跨例驻留累积。
- 修复：①逐例 `gc.collect()` + 大对象显式 del；②报告记录 VmHWM 峰值 RSS/耗时；③**评测走宿主独立进程**（宿主 .venv 全链路可用，生产容器不再承载评测负载）；④smoke（5 例）/golden（28 例）分档，CI 用 smoke。
- 实测：28 例完整跑 **exit 0，峰值 RSS 375.6MB，耗时 277s**，无 OOM。

## I. Kill Switch（真实测试）

| 场景 | 结果 |
|---|---|
| CONTEXT_L5_ENABLED=true + 7930 token 极端上下文 | L4 触发（-898 tok）→ L5 真实调用 → 摘要落库（503 tok）→ 水位线推进 → 原始 chat_messages 不变 → 无溢出 |
| CONTEXT_L5_ENABLED=false 同上下文 | **0.03s 返回，摘要 API=0，确定性降级放行，无溢出**，`context_l5_total{status="disabled"}` 计数 |

## J. L5 Failure（零阻断验证）

| 故障 | 结果 |
|---|---|
| timeout（0.001s） | 立即回退，水位线不动，消息不变，无异常外泄 |
| provider 不可用（绑定未注册模型 qwen3.7-plus） | proxy 拒绝覆盖→回退主模型继续；另一路径（注册表为空时）实测 validation error → 安全回退，主链路不受影响 |
| empty summary | 单测覆盖：回退、不落库 |
| DB failure | save 失败 → rollback + 回退（单测覆盖） |
| 不自动降级到主模型重试 | §九原则：调用失败只回退确定性裁剪，不换模型 |

## K. L5 ROI（新模型口径）

| 项 | 值 |
|---|---|
| 单次 L5 API tokens | ~560（450 in + 110 out）；极端大 delta 顶格 512 out |
| 单次替换的原始历史 | ~2000-2500 tok（7930 → 折叠+摘要后 ≈ 5000） |
| 3-turn / 5-turn / 10-turn 累计净省 | 每次 L5 净省 ≈1500-2000 tok active context；后续轮次因 L2 封顶 2048，增量省 token 主要体现为「不丢事实」而非降 token |
| break-even turns | **1**（首次触发即回本：~560 tok 成本 << ~2000 tok 替换收益） |
| 备注 | L5 的首要价值是事实保真（vs 确定性 hard trim 丢事实），token 节省是次级收益 |

## L. Tests

- `backend/tests/context_budget/ + memory/ + prompts/ + config/ + router_prefilter + registry/layer/adr0001`：**328 passed, 0 failed**（--no-cov）。
- 新增 `test_phase5_hardening.py` 23 例（配置/裸金额/kill switch/关 thinking/失败回退/角色解析链）。
- 既有无关失败（非本任务引入）：`test_llm_provider_passthrough.py` 3 例（minimax base_url 断言，宿主 env/DB 状态污染，本任务未触碰该文件/provider）。
- evaluation smoke：golden --smoke 5 例全绿。

## M. Git

| Commit | 内容 |
|---|---|
| 0f6ab43 | perf(context): context_compactor 角色注册 + L5/ProtectedFacts 低基数指标 |
| 9108bc2 | fix(context): L5 生产硬化 —— 配置正式化 + 裸金额保护 + kill switch 观测 |
| ec2db35 | test(context): Golden 评测稳定化 —— 独立进程 + 内存卫生 + 裸金额案例 + smoke/golden 分档 |
| （本笔） | B06 测试语义修正 + 本报告 |

剩余 dirty files（`backend/infra/llm/proxy.py`、`resolved_model.py`、`frontend*/tsconfig.json` 等）均为**其他会话**改动，本任务全程路径限定未触碰。
运行 commit 核对：app/worker 容器已 `docker compose up -d --build app worker` 重建，容器内 grep 确认含 context_compactor/裸金额/新指标代码，健康检查通过。

## N. Production Verdict

```text
PHASE5_PASS=true
CONTEXT_BUDGET_PRODUCTION_READY=true
```

L1-L5 上下文预算管理正式完成（Feature Complete / Production Ready）。
按约定不再新增 Context Phase、不做 L6、不重构核心；后续精力回归 Agent 主链 / RAG / SQL / 客服 / 选品 / 任务编排 / 权限。
