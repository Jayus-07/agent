# TASK 移交:周 PR 回顾评审的后续处置(2026-11-11)

> **本文件是上一会话写给下一会话的交接提示词。**
> 接手方式:通读本文 → 把「三、待办」呈给用户裁决 → 按裁决执行。
> 标注【用户裁决】的事项在拿到明确裁决前**不得动手**;标注【勿动】的一律不碰。

---

## 一、背景(30 秒版)

2026-11-03 ~ 11-10 仓库合入 137 个 PR(全单人自审自合)。上一会话完成三件事,均已收口:

1. **全量回顾评审**:7 组主题并行深评(记忆/管理端+预算/两批安全审计/评测/客服/旅游供应链),产出问题清单(P1×3、P2 若干、P3 杂项),全部有文件行号级证据。
2. **修复并合并 7 个 PR**:
   - #435 fastapi 0.161 路由内省修复 + model_revive 定价对齐(周回归两个 CI-only 失败的根因:lock 版 fastapi 的 include_router 惰性挂载)
   - #436 周回归 18 红分诊修复(9 组测试漂移)+ **拒答候选 NameError 真实 bug**(service.py `_build_refusal_reply` 漏传 tenant_id 被吞)+ 迁移 147(CS 六表只读误授权回收)+ cs_faq 真库回归
   - #437 评审 P1 三件:记忆证据门归一顺序(客服显式记忆曾整体失效)/ map 工具 backend Key 泄漏 fail-closed / 漏斗 graph 租户透传(含 reporter fail-closed)
   - #438 安全卫生:cs_knowledge 空租户 403 / internal_ai 空 actor 拒绝 / api_keys 未知 Key DoS 防线(形状快筛+负缓存)
   - #442 smoke 触发面补 tools/memory/selection_funnel(含清单守卫防门禁空转)
   - #446 迁移 148:product/"order"/inventory/crawler 默认授权收官(解决 #440)
   - #447 监控池平台全局口径落档(解决 #441)
3. **收尾核验**:venv 对齐 lock、worktree/分支清理、issue 状态核验。

## 二、当前状态(2026-11-11 收尾时,接手后先复核)

- main 在 5fce573 之后**持续有其他会话合并**——勿假设 HEAD 未变,动手前先 fetch。
- 本地 venv 已对齐 requirements-lock(fastapi 0.161.1 / sqlglot 30.24.0 / otel 1.45.1),`pip check` 干净;**pip 报 ProxyError 时先试 `no_proxy="*"`**(根因是系统代理被 pip 拾取)。
- 本地 agent_business 库:迁移 147/148 的 REVOKE 已手工生效,`schema_migrations` 未登记——幂等,下次 init_db 正常补登记,无需处理。
- **主工作区(main worktree)有其他会话在途改动**(`frontend-cs/package-lock.json` modified)与在途 PR(#443 域额度 / #449 复审报告 / #450 cs CI)——【勿动】。
- 权威全量基线 = 下周一 11-16 定时 Full Regression(03:00 UTC),勿在全量之外擅自 dispatch。

## 三、待办(按优先级)

### 1.【必修 · 部署前置 · 需 ECS 权限】GATEWAY_TRUST_SECRET 生产确认

- **风险链路**(评审实证):直连后端 8000 且未配 `GATEWAY_TRUST_SECRET` 时,可伪造 `X-Auth-Type: api-key`,借 api-key 通道 `tenant_id=None` 语义(`backend/app/api/identity.py:69-75`)获得**跨租户服务视图**(`cs_admin.py` 396-397 一带)。网关 Lua(`apisix/plugins/gateway-auth.lua:48`)剥离伪造头的前提是流量走网关。
- **动作**:确认生产 ECS 已配 `GATEWAY_TRUST_SECRET`(backend 与 APISIX 同次变更、backend 先就绪,见 #407/#408 注释);实测一次**绕过网关直连 8000** 的请求被拒(401/403);把「密钥确认 + 直连拒绝实测」写进部署 checklist(生产/本机 4C8G README 或 commands.md)。
- **若查实未配** → 升级为 P1 立即修(配置 + 补检测),不得拖到下个批次。
- 上一会话无 ECS 权限,此项只能由有权限的会话/人执行。

### 2.【必修 · 红线决策 · 用户裁决】自动模拟退款豁免确认流

- **事实**:`backend/customer_service/experts/action.py:601-660` 对重复扣款模拟退款 `auto_duplicate_refund_simulation_eligible` 成立即直接执行,**不经确认卡**;兜底为双开关默认关(`backend/config/customer_service.py:252-258`:`CS_AUTO_DUPLICATE_REFUND_MODE=disabled` + 金额上限 0)。
- **冲突**:AGENTS.md 红线「有副作用的 Tool 必须经过现有 Governance、确认/审批和幂等机制」——机制上红线悬空,靠默认值撑着。
- **裁决二选一**:(a) 纳入确认卡流程(小代码改动,参照 #340 的确认流模式);(b) 治理文档明文豁免(写清启用条件、金额上限、审计要求,放 docs/governance 或 customer-service.md)。
- 拿到裁决即落地,一个小 PR 收口。

### 3.【必修 · 流程决策 · 用户裁决】付费评测最小节奏

- **事实**:#353 起 RAG Live 改显式 opt-in,仓库内**零自动触发路径**;真实 LLM 质量自 2026-11-07 后无人验证,且无提醒机制。
- **动作**:用户定最小节奏(建议:每周一手动 dispatch 一次 `RAG PR Smoke` + `run_live_eval=true`,写进值班/周报清单;可选加「距上次 Live 天数」暴露)。拿到节奏即落档(commands.md 或 evaluation-governance.md 一小节)。

### 4.【hygiene PR 候选 · 裁决后一次清】非必修但有证据的 6 项

全部有兜底、非阻塞,攒一个小 PR 批量修(每项独立 commit):

| 项 | 位置 | 修法 |
|---|---|---|
| `_resolve_amount` 对 `Decimal("Infinity")` 抛未捕获 TypeError→500 | `backend/app/api/routes/budgets.py:57-59`(try 块外的 exponent 比较) | 补 `number.is_finite()` → 422 |
| handoff 永不置终态,残留累积(#296 只过滤了症状) | handoff 状态机,残留 human_active 行 | 挂 maintenance 定时对账(参照既有机制) |
| eval 采样台账 `_persist` 软失败零告警 | `backend/evaluation/online_sampler.py:364-366`、`backend/tasks/eval_sampling_tasks.py:36-49` | 连续 persisted=False 计数上报 worker_metrics |
| cs 数据集版本锁 `"2.2"` 散布 ≥5 处,升版必漏 | `backend/customer_service/knowledge/evaluation_scope.py:56`、`backend/evaluation/runners/cs.py:519`、datasets manifest、suites JSON | 跨文件版本一致性守卫测试 |
| Toast a11y 属性三端拷贝漂移 | `frontend-admin/src/components/shared/Toast.tsx`、`frontend-cs/...` 补齐 `role="status" aria-live="polite"` | 同步三端拷贝 |
| admin 测试环境敏感(本地带 .env 必失败) | `backend/tests/api/test_admin_mcp_api.py:235` 断言 `(404,500)` 实得 403 | monkeypatch 豁免开关或显式断言豁免前提 |

### 5.【收纳 issue · 裁决后开】P3 杂项(不单独开 PR)

api_keys `scopes` 字段无消费;travel `tool_cache.py:26` 缓存键租户回退与 `plan_service/plan_store` 的 `tenant_id="default"` 参数默认值 ~15 处(防御纵深);#304 PG 标识符白名单同型拼接残留 6 处(`backend/rag/indexing/chunk_store_pg.py:43` 与已收口 store 共用同一 env 变量,另有 market_research/inventory/pricing/quota/trace_retention,且 7adc42c 又新增实例);HOT_CITIES ⊆ CITY_DIRECTORY 无守卫;cs_faq 桩已由真库测试防漂移(不再立项)。一个 issue 收纳,写清来源(周回顾评审)与各自兜底。

### 6.【可选 · 用户点头才跑】3-suite 定向回归

`tools-security-obs`(map 工具)/ `misc`(selection_funnel)/ `customer_service`(拒答候选)——本会话修复暴露且未在 CI 看过的 suite。**勿跑全量**:其他会话在持续合并,归属会乱;权威基线是周一 11-16 定时全量。

### 7.【用户自理】#67 过期看板

"[Delivery Control] 施工队列(2026-10-14)"最后更新停在 10-15,引用的 main/PR 状态全部失效——关或留,用户决定,会话不要擅动。

## 四、边界与红线

- 【勿动】主工作区在途改动、在途 PR #443/#449/#450、其他会话的全部 worktree(`git worktree list` 里 codex/* 与 agent-wt-* 的非本任务项)。
- 【勿动】batch6/7 排期与 R1~R7 复审清单(#448 刚落,归其他会话)。
- 本地 venv 已对齐 lock,本地结果≈CI;但涉及 fastapi/sqlglot 行为差异的结论仍以 CI 为准。
- 工具契约:改任何 `@tool` docstring/签名,记得 `python -m backend.scripts.gen_tool_contract_lock` 重新生成并 `--check`(#437 教训)。
- 所有改动走 PR 合并 main;提交/push 被 Mimosa 闸拦且拦截项全在 `test_auth_middleware.py`(预存,#384)时,确认与自己的暂存面无关后重试即可。

## 五、验收标准

1. 三项必修各有落痕:A=部署 checklist + 实测记录;B=确认流代码或治理豁免文档;C=节奏落档。
2. hygiene PR 合并后,表中 6 项全部有回归测试或守卫。
3. 收纳 issue 开立且列明各项兜底。
4. 全程零触碰第四节边界;不新增 PAUSED/BLOCKED 在途状态。
