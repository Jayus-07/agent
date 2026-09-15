# 成员 2 · 文档收口写手（ε 线）

**可写路径**：`docs/**`、`.gitattributes`、删除 Java/本地栈时代的 `.bat`
**禁止**：`backend/**`、`frontend/**`、`apisix/**`、任何容器操作

---

## 任务 B9：收口工作区在途产物

工作区有一批**已成型的成果未入库**。本任务只处理**无争议**的部分。

### 明确要做的

| 类型 | 内容 | 处理 |
|---|---|---|
| ` M` 计划回填 | `docs/RAG优化实施计划.md`、`docs/gateway-apisix-migration-plan.md`、`docs/superpowers/plans/2026-09-15-skill-integration-plan.md` | 报告作者对计划表的回填（B4 完成、4.1b 运行时验收、批次 2 状态），**内容有意义** → 路径限定提交 |
| ` D` 启停脚本 | `activate_python.bat`、`restart_all.bat`、`restart_local.bat`、`start_all.bat`、`stop_all.bat` | Java/本地栈时代产物，删除合理 → 提交删除 |
| `??` 报告 | `docs/reports/`、`docs/项目进度报告-2026-09-15.md`、`docs/2026-09-15-实测修复与优化报告.md`、`docs/2026-09-16-DB-schema实测与待办报告.md`、`docs/2026-09-16-跨会话交接报告.md`、`docs/2026-09-16-旅游域图实测模拟与缺陷报告.md`、`docs/RAG优化实施-阶段总结报告.md`、`docs/superpowers/plans/2026-09-15-unified-roadmap.md` | 成果/报告 → 入库 |
| `??` 验收产物 | `data/runtime_acceptance/`、`data/market_research_*_0915.md`、`data/docs/policy_general/general/` | 先**逐个判断**是真验收产物还是临时输出，再决定入库或忽略 |
| `??` 垃圾 | `.coverage.DESKTOP-*` | **临时产物，可删**（或加入 `.gitignore`） |

### 明确**不要**做的

| 内容 | 原因 |
|---|---|
| `scripts/ensure_dbs.py` | **归属待用户拍板**（与 alembic 单轨路线二选一，见手册所引事实源 §7-#4/#5）→ **不要提交** |
| `backend/travel/*` 6 文件 | 归 γ 线（波次 2） |
| 任何 `backend/**` / `frontend/**` 改动 | 不属你的文件域 |
| `stop_frontend.bat` (`??`) | 与其他 `.bat` 不同，它是**新增**而非删除 → 单独判断后再动 |

### 通过标准

```bash
export PATH="/usr/bin:/bin:/usr/local/bin:$PATH"
cd "D:/Program Files/workplace/agent"

git diff --cached --name-only     # ① 动手前必看：有没有别人 staged 的东西
git status --short                # ② 确认你要提交的清单与上面一致
# 提交后
git log --oneline -3
git show --stat HEAD              # ③ 逐行核对：是不是只有你的路径？
```

**硬要求**：
- 提交**一律路径限定**：`git commit -m "..." -- <paths>`
- **未跟踪的新文件必须先 `git add`**，否则 `git commit -- <path>` 会报 `pathspec did not match any file(s) known to git`
- 建议**按主题拆 3~5 笔**（计划回填 / 报告入库 / .bat 删除 / 验收产物），不要一坨

### 提交信息参考

```
chore(docs): 收口 09-15/09-16 在途产物：计划回填 + 报告入库 + 启停脚本清理

- 计划回填：RAG优化实施计划、gateway-apisix-migration-plan、skill-integration-plan
- 报告入库：docs/reports/、项目进度报告、DB-schema 报告、跨会话交接报告、旅游域实测报告、
  RAG 阶段总结、unified-roadmap
- 删除：Java/本地栈时代的 5 个 .bat 启停脚本
- 未提交：scripts/ensure_dbs.py（归属待拍板，与 alembic 路线二选一）
```

**回报格式**：提交了哪几笔（hash + 文件数）/ 哪些**故意没提交**及原因 / 遗留给人类的判断项。
