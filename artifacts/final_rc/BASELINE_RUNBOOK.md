# Final RC 全量回归口径与运行手册（实测锁定，v3）

> 本文件记录 Final RC 轮全量回归的执行口径、环境病态事实与处置方法。
> STOP E 最终复跑必须使用同一口径。

## 一、锁定口径（宿主机分块）

```bash
cd "D:\Program Files\workplace\agent"
PYTHONPATH=backend PGPORT=5433 PGCONNECT_TIMEOUT=10 PYTHONUTF8=1 \
  ./.venv/Scripts/python.exe -m pytest -q --tb=short -n 4 --continue-on-collection-errors <块路径>
```

## 二、口径依据（2026-09-25 实证）

1. **PYTHONPATH=backend（必须）**：根 `tests/`（无 `__init__.py`，namespace）会抢占
   `import tests`，致 13 个模块 collection error 中断全量。设后 7397 collected 全通。
2. **PGPORT=5433（必须）**：docker 权威库宿主映射 127.0.0.1:5433；`.env` 的 PGPORT=5432
   是容器视角。
3. **-n 4**：与上轮 25m34s 对齐（单进程实测 9min/5%，全量 ~3h，非上轮口径）。
4. **PGCONNECT_TIMEOUT=10**：psycopg3 `waiting` 路径实测**不读该 env**（见 §三.4），
   仅对 libpq 直连路径有效；保留作部分兜底。真正兜底见 §四。

## 三、环境病态事实（全部实测，历次挂死同根因）

1. **宿主→docker PG 的 vpnkit 端口转发链路病态**：TCP connect 瞬时成功（0.00s），但
   psycopg 握手阶段挂死在 `waiting.wait_conn select`（无超时无限等）。py-spy 实锤
   4 worker 同栈挂死 ≥3 轮。
2. **PG max_connections=100**：业务容器常驻 ~52 + 测试 4 worker 无上限直连 →
   `too many clients already` 周期性出现（cs 块实拍）。
3. **DB 密集测试的连接风暴触发挂死**：幂等账本/STOP E/booking fixture 每用例、
   booking store 每业务操作各建新连接（`store.py:100` → conftest `_factory`）。
4. **PGCONNECT_TIMEOUT 对 psycopg3 waiting 路径无效**（booking 挂死时 env 已设仍挂）；
   psycopg2 路径同样不可靠。
5. **GNU timeout（Git Bash）--signal=KILL 在 Windows 不生效**：无法终止 pytest 树。

## 四、稳定跑法（按优先级）

| 场景 | 方法 |
|---|---|
| 常规块 | `-n 4` + PGCONNECT_TIMEOUT=10 + log 停滞看护（8min 无增长即挂死信号） |
| DB 密集文件（幂等账本/STOP E 族） | `-n 1` 串行（rootc-dbserial：68 passed 全绿实证） |
| booking 族（conftest 自建连接） | **容器内第二路径**：`MSYS_NO_PATHCONV=1 docker exec agent-app-1 sh -c "cd /app && PGPORT=5432 PYTHONPATH=/app RAG_EVAL_NO_ISOLATION=1 /opt/venv/bin/python -m pytest backend/tests/travel/booking -q --tb=short -n 1"`（需 root 装 pytest/pytest-xdist/pytest-asyncio，运行时注入；43 passed 25.4s 实证） |
| 失败甄别 | node 级分批 `-n 2` 复跑：复绿=环境污染剔除；稳定失败=persistent 债 |

## 五、产物索引

- `chunks/` —— 15 个块日志 + summary（含各次挂死残段）
- `discriminate/` —— 甄别批 1/2a/2b/3/4/5 日志（per-node）
- `baseline-failures-raw.txt`（174）→ `baseline-failures-persistent.txt`（89）+
  `baseline-envflaky-recovered.txt`（85）
- `baseline-envflaky-serialproof.txt`（24，串行复跑全绿证明子集）
- 历次失败跑残段：`baseline-collect-broken-run1.log`（无 PYTHONPATH 反证）、
  `baseline-singleproc-slow-run2.log`、`baseline-hang-run3-partial.log`、
  `chunks/root-c.hang-partial*.log`、`chunks/travel.hang-partial.log`
