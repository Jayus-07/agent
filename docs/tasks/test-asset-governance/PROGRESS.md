# PROGRESS：测试资产治理与回归提速

## 当前状态

Phase 1 完成；Phase 2 已完成可执行部分（7/7 例可立即处理的失败全部消除）；
Phase 3/4 待他会话收口。

---

## 已完成与证据

### Phase 1：只读审计（未改任何文件）

- 全量收集：**10,542 tests / 92.66s**，0 collection error。
- 规模：935 个 Python 测试文件 / 135,417 行 / 9,267 个测试函数；150 个前端测试文件。
- 产出 KEEP/UPDATE/MERGE/DELETE/FIX/REVIEW 分类清单（见 AUDIT.md §5）。
- **结论：DELETE 类为 0**。排除 6 类误判（见 AUDIT.md §4）。

### Phase 2：分批施工（只改测试资产 + 测试配置）

| # | 文件 | 改动 | 验证 |
|---|---|---|---|
| 1 | `backend/tests/conftest.py` | 隔离 `LOG_FILE` | 729 collected / 0 error（原 15 errors） |
| 2 | `customer_service/test_llm_cascade.py` | 新增 `_fake_active_llm()`，改 6 处 patch 接缝 | 3 failed → **14 passed** |
| 3 | `context_budget/test_stop_b_p0.py` | 注入 encoding 锁 compatible + 新增降级用例 | — |
| 4 | `context_budget/test_stop_a_hard_gate.py` | 拆分锁定硬拒/软降级两态 | **235 passed / 0 failed** |
| 5 | `memory/conftest.py` + 3 文件 | schema 探测按会话缓存 | **315.2s → 180.6s（−42.7%）** |
| 6 | `customer_service/test_state_transition.py` | 断言收敛到目标症状 | 1 failed → **9 passed** |
| 7 | `pytest.ini` | 登记 timeout 用法 | 12 passed，无告警 |
| 8 | `pyproject.toml` | 声明 `pytest-timeout>=2.3` | TOML 解析通过 |

**收口验证：**

```text
context_budget                  235 passed / 0 failed
customer_service               1304 passed / 0 failed   (原 4 failed / 1300)
memory                           93 passed / 180.6s     (原 315.2s)
sql+context_budget 收集         729 collected / 0 error
依赖方抽检（direct_executor+infra）  305 passed
收口 L2（5 模块）                682 passed / 0 failed / 238.5s
```

**纪律遵守：** 未删除任何用例；未新增 skip/xfail；未放宽断言；未改生产代码。
净新增 4 条用例，锁定此前无覆盖的分支（tiktoken 降级路径 + 软降级保内容）。

### 关键证据（非猜测）

- **批次 2 根因**：独立探针实测
  `patch _resolve_active_llm → calls: 1`（命中 fake）
  vs `patch get_llm → calls: 0`（未拦截，真打网络）。
- **批次 6 根因**：bisect 定位污染源为 `test_p5_stopcs_a_realpg.py` 遗留的
  `_ProactorBasePipeTransport.__del__ → RuntimeError: Event loop is closed`。

---

## 阻塞

### 阻塞 1：他会话施工中间态（70 例失败，本轮未动）

- `api` 45 例：`/travel/plan`、`/travel/preferences` 已从修改后的
  `backend/app/api/routes/travel.py` 移除；测试期望 401 实得 **404**。
- `orchestration` 15 例：`runner.py`/`direct_executor.py`/`builder.py` 等在改，
  且有未跟踪新文件 `sse_event_sink.py`。
- `rag` 10 例：`pipeline.py`/`chain.py` 在改。
- 另有 `sql/test_sql_skill_followup.py` 1 例：他会话给 `skills/sql/skill.py`
  加了 `event_sink=` 参数，测试的 `FakeAgent` 未同步。

**处置：全部阻断，等收口后复测定性。**

### 阻塞 2【2026-10-10 新发现】：语法损坏阻断 travel/orchestration

`backend/orchestration/router/capability_router.py:248` 存在**字面量 `\n`**：

```text
E   File ".../capability_router.py", line 248
E     \n
E   SyntaxError: unexpected character after line continuation character
```

- 该文件 **3:36:57 被非本会话修改**（`git status` 为 M）；HEAD 版本正常结束于 `__all__`。
- 扩散：`travel` 1259 collected / **8 errors**；`sql` 3 errors；
  `customer_service` 2 errors；`memory` 1 error。
- **已排除本会话所致**：我改的 9 个文件全部通过 AST 校验，且未触碰该文件。
- **修复成本：删除该行（一行）。**
- **本会话未代改**：属他会话在途工作，按 AGENTS.md「保留其他会话的修改」上报等待指派。

### 阻塞 3：离线环境

- 无法安装 `pytest-timeout`（pip ProxyError / SSL 失败 / 无本地 wheel 缓存）。
- 无法重新生成 `requirements-lock.txt`（生成方式 `pip freeze --all` 依赖已安装环境）。
- 影响：4 个 CI workflow 均从 lock 安装，**lock 未重生成前 CI 无该插件**。
- 前端 vitest 因沙箱 `spawn EPERM` 无法运行。

### 阻塞 4【2026-10-10】：本任务报告被删除

`docs/reports/` 下 **199 个文件被其他会话删除**（含本任务报告，仅剩 `.gitkeep`），
且未迁移到其他目录。报告为未跟踪文件，无法从 git 恢复，已依据本会话实测记录重建至本目录。

---

## 与既有结论的更正（诚实标注）

1. **Phase 1「0 个 xfail」有误**：当时仅静态统计 `pytest.mark.xfail`（0 个），
   遗漏运行时 `pytest.xfail()`。实测 `travel/test_intent_llm_golden.py` 有 **9 个 xfailed**
   （词表盲区登记，属"逐例登记而非静默放过"的有意设计）。
2. **`travel` 34,876s 不是有效性能基线**：测量时处于上述 import 损坏态，
   数字不可用于性能归因，需修复后重测。

---

## 待用户决策

1. **是否授权本会话修复 `capability_router.py:248`**（一行删除）。
   当前它阻断 travel/orchestration 全部验证，也污染跨模块计时。
2. **CI 是否加入 pytest 门禁**（会改 `.github/workflows/`）。
3. **`orchestration`（31:44）性能归因**建议等收口后单独测量。

---

## 下一步

1. 待用户指派后修复 §阻塞 2 的一行语法损坏，重测 travel 真实耗时。
2. 等他会话收口，复测 70 例阻断项并定性。
3. 联网后按 AUDIT.md §11 三步收口 pytest-timeout（装插件 → 验证后落 ini 键 → 重生成 lock）。
