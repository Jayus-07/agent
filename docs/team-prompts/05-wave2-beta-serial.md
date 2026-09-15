# 波次 2 · 单会话串行指令（**不建议组队**）

## 为什么这一波不用 Team

- **B1**（定位卡死用例）会长时间独占后端与容器；
- **B2**（旅游域收口）需要 `rebuild app` 容器，会打断 B1；
- 两者**互斥**，且此时其余三条线无事可做。

→ 组队的协调开销全花在「等」上，还会重演 09-16 那次「跑测试期间改后端 → 整轮作废」。
**单会话串行更省、更快。**

---

## 执行顺序

### 第 1 步 · β：B1 定位卡死用例（独占后端 + 容器）

> ⚠️ **开工前先确认这个任务没被别人占**。截至 01:50，并发会话**正在执行 B1**
> （01:42 产出 `logs/collect_order_0916.txt`，01:43 的 `collect_order_exact.txt` 为 0 字节）。
> 若它还在跑 → 本步**不要启动**，改为等结果或直接跳到第 3 步。

**已有线索（本次核验精确化，见手册 §8.1）**：

- 最后完成的用例 = **#2088（54.69%）** ∈ `backend/tests/rag/test_tracer.py`
- **首条未完成 = #2089（54.71%）**；`test_tracer.py` 覆盖 #2086~#2143（58 条），停在**第 4 条**附近
- ⚠️ 精度上限：stdout 块缓冲 → 停滞窗口严谨表述为 **#2089~#2160**
- **8 个失败用例里 7 条属 `backend/tests/rag/test_indexer_trace.py`**（该文件共 16 条），1 条属 `test_embed_batching.py`
- 🎯 **最强假设**：失败簇与卡死**同属 tracer 主题** → 可能同一根因（共享 collector / subscriber 队列未释放致内存累积）

```bash
export PATH="/usr/bin:/bin:/usr/local/bin:$PATH"
cd "D:/Program Files/workplace/agent"

# ① 先跑嫌疑最小的那对文件，-v 逐条打印，看停在哪一条
./.venv/Scripts/python.exe -m pytest \
    backend/tests/rag/test_tracer.py backend/tests/rag/test_indexer_trace.py \
    -q -p no:randomly --no-cov -v

# ② 仓库未装 pytest-timeout → 建议装（纯 Python，新装包不会触发 safe-delete FAIL_CLOSED）
./.venv/Scripts/python.exe -m pip install pytest-timeout
#    之后可用：... --timeout=120 --timeout-method=thread 自动掐死卡死用例

# ③ 若 ① 不卡 → 用 -x 从停滞窗口往后分段收敛
./.venv/Scripts/python.exe -m pytest backend/tests/ -q -p no:randomly --no-cov -x \
    --deselect <已知失败用例>

# ④ 全量（只在 ③ 定位干净后才跑）
./.venv/Scripts/python.exe -m pytest backend/tests/ -q -p no:randomly --no-cov
```

**通过标准**：能跑完整轮，并给出失败用例的分组清单（用于回答事实源 §7-#2「17 个失败如何分工」）。

**熔断**：进程 RSS 持续涨 + 日志长时间无写入 = 卡死 → 上报，**不要一味重试**。

---

### 第 2 步 · γ：B2 旅游域收口（需 rebuild 窗口）

工作区已有 6 个文件、**+135 / -7**（`backend/config/travel.py`、`backend/travel/{graph_state,repair,supervisor}.py`、
`backend/tests/travel/{test_repair,test_travel_graph}.py`）。其中 **4 条新用例已写好但从未跑过**：

```
test_unfixable_marks_stalled / test_successful_repair_clears_stalled
test_stalled_repair_terminates / test_recursion_limit_outlasts_step_guard
```

```bash
./.venv/Scripts/python.exe -m pytest backend/tests/travel/ backend/tests/rag/test_checkpointer.py \
    -q -p no:randomly --no-cov
```

> ⚠️ 这 6 个文件在工作区是 **CRLF**、索引是 LF（`.gitattributes` 已把 `*.py` 定为 `eol=lf`）
> → 提交后 `git status` 会显示干净，属正常归一化，**不要当异常处理**。
>
> ⚠️ 容器：收口需 `rebuild app`。**动手前先 `docker ps` + `netstat -ano | findstr :8000`**，
> 确认宿主 uvicorn 与容器不是同时监听（否则请求随机分流）。会打断别人进度就先报告。

**通过标准**：4 条新用例通过 + `backend/tests/travel/` 全绿 → 路径限定提交。

---

### 第 3 步 · β：B7 / B14 / B15

| 批次 | 内容 | 备注 |
|---|---|---|
| **B7** | decision_log 拍板/表现回填 API + 前端入口 | 存储层已在（`selection_decision/store.py:207/217`），**只差路由**；前端入口 α 后续补 |
| **B14** | Skill 批次 4（CPR 吸收 + 证据分型） | 无依赖，可立即开工 |
| **B15** | py 单端 dept claim（原三端简化为一端） | 工作量最小的架构收益项 |

---

### 插空 · 窗口型

| 批次 | 内容 |
|---|---|
| **B8** | mcp-service healthcheck：compose 给该服务加 `healthcheck.test` 指 **8091**（app/rag/postgres/redis 都有覆盖，**它是唯一漏配的**）→ `docker ps` 变 healthy |
| **B10** | dev 代码卷：`docker-compose.override.yml` 挂 `backend/` → 改一行码免 rebuild |

---

## 两条一定要遵守的

1. **跑全量测试期间不要改 `backend/**`、不要动容器**（已实际违反一次，整轮作废）。
2. **不要擅自 kill 别的会话的进程** —— 判定卡死后先上报，让人类决定。
