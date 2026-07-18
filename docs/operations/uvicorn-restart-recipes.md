# uvicorn 重启 Recipes

> 来源：2026-07-18 多轮 SSE bugfix 实战经验。**核心教训：`--reload` 经常卡住新 worker，必须手动 force kill**。

---

## TL;DR

```bash
# 改完 backend 代码后，**永远**用以下步骤重启（不要相信 --reload）：

powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"
sleep 4
cd backend && nohup ../.venv/Scripts/python.exe -m uvicorn app.server:app --port 8000 > /tmp/uvicorn.log 2>&1 &
sleep 10
powershell -Command "(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count"
# 期望：1（只有一个进程监听 8000）
```

---

## `start_all.bat` vs `--reload` 决策

| 场景 | 推荐 |
|------|------|
| 本地开发（频繁改代码） | `start_all.bat` 已带 `--reload`（2026-07-18 修改后） |
| 测试 SSE / 调试具体 bug | **不用** `--reload`，干净启动 |
| 生产 / Docker | gunicorn + workers，**永远不要** `--reload` |

`start_all.bat` 现状（已 commit `0cf7346`）：

```bat
start "Agent-Backend-8000" /D "%ROOT%\backend" cmd /k "..\.venv\Scripts\python.exe -m uvicorn app.server:app --port 8000 --reload"
```

---

## `--reload` 卡住的症状

```
INFO:     Will watch for changes in these directories: ['...']
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [4860] using WatchFiles
WARNING:  WatchFiles detected changes in 'app\api\routes\rag.py'. Reloading...

# ⚠️ 没有下一行 "Started server process [...]"
```

`Reloading...` 之后**没有**新的 `Started server process`，意味着：

- reloader 进程检测到文件变化
- 但**新 worker 进程没启动成功**
- 老 worker 继续运行（带旧代码）
- 测试看到的行为 = **旧代码行为** → 误以为修复无效

### 为什么卡住

常见原因：
- `_do_index_sync` 里的 HuggingFaceEmbeddings / Chroma 在 executor 线程挂起
- `run_in_executor` 没正确终止
- uvicorn 的 watchdog 检测到变化但 reload 卡在子进程清理

### 验证 reload 是否成功

```bash
# 1. 看 worker 启动日志
grep "Started server process" /tmp/uvicorn.log | tail -3

# 2. 看进程 PID（每次 reload 应该是新的 PID）
powershell -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Get-Process -Id \$_.OwningProcess } | Select-Object Id, StartTime"
```

如果 PID 没变 + StartTime 是旧的 → reload 没生效。

---

## 完整 force restart 流程（Windows）

```powershell
# Step 1: 列出所有 Python 进程
powershell -Command "Get-Process python | Select-Object Id, StartTime, CommandLine"

# Step 2: 杀光（强制）
powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"

# Step 3: 等端口释放
Start-Sleep -Seconds 3
powershell -Command "(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count"
# 期望：0

# Step 4: 干净启动（no --reload 避免再次卡住）
cd backend
nohup ../.venv/Scripts/python.exe -m uvicorn app.server:app --port 8000 > /tmp/uvicorn.log 2>&1 &
Start-Sleep -Seconds 12

# Step 5: 验证
powershell -Command "(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count"
# 期望：1
```

### Bash 等价

```bash
pkill -f "uvicorn app.server" || true
sleep 4
cd backend
nohup ../.venv/Scripts/python.exe -m uvicorn app.server:app --port 8000 > /tmp/uvicorn.log 2>&1 &
sleep 12
netstat -ano | grep ":8000.*LISTENING" | head -1
```

---

## 多进程残留陷阱

**常见问题**：之前 session 启动的 uvicorn 没杀干净，导致新进程启动后**端口冲突** → 新进程 worker 启动失败 → 但老进程仍监听 8000。

```bash
# 诊断：可能有多个进程都在监听 8000
powershell -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Get-Process -Id \$_.OwningProcess } | Select-Object Id, StartTime, CommandLine"
```

如果看到**多个**进程 → 全部 kill 再重启。

或者用 `wmic` / `Get-WmiObject Win32_Process` 查命令行的精确路径：

```powershell
Get-WmiObject Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId, CommandLine
```

---

## 调试日志模板

```bash
# 启动
nohup .venv/Scripts/python.exe -m uvicorn app.server:app --port 8000 > /tmp/uvicorn.log 2>&1 &

# 启动后立刻看：
sleep 5
echo "=== Startup log ==="
cat /tmp/uvicorn.log | grep -E "Uvicorn running|Started server process|Started reloader|Application startup"

# 上传 + 订阅
UPLOAD=$(curl -s -X POST http://localhost:8000/rag/upload -F "file=@test.txt")
UPLOAD_ID=$(echo "$UPLOAD" | python -c "import json,sys; print(json.load(sys.stdin)['upload_id'])")
echo "=== SSE output ==="
timeout 5 curl -s -N "http://localhost:8000/rag/upload/${UPLOAD_ID}/stream"

# 看后台 indexer 日志
echo "=== Indexer logs ==="
grep -E "index_|SyncResult|Loading weights" /tmp/uvicorn.log | tail -10
```

---

## `start_all.bat` / `restart_all.bat` 行为说明

| Bat | 启动后端方式 | 适用场景 |
|-----|--------------|---------|
| `start_all.bat` | `cmd /k "uvicorn ... --reload"` 开新窗口 | 正常 dev 启动 |
| `restart_all.bat` | 调 `stop_all.bat` + `start_all.bat` | 重启全套服务 |

**已知问题**：
- `start_all.bat` 用 `cmd /k` 开新窗口 → 关闭窗口即停止服务
- 窗口可能被误关 → 服务停掉
- 端口冲突时 `start_all.bat` **不会自动处理**（只有警告）

**生产建议**：改用 `nohup` 后台启动 + log 重定向（见上方流程）。

---

## 何时用 `--reload`

✅ **用**：
- 单人本地开发
- 改 Python 代码后立即看效果
- 不涉及 SSE / 长连接测试

❌ **不用**：
- 调试 SSE / WebSocket / 长连接
- 涉及 subprocess（`run_in_executor` 阻塞）
- 验证多进程 / 端口冲突
- 性能测试

---

## 关键命令速查

```bash
# 查端口 8000 占用
powershell -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue"

# 杀光 Python
powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force"

# 等端口释放
Start-Sleep -Seconds 3

# 干净启动 uvicorn（带 reload）
cd backend
nohup ../.venv/Scripts/python.exe -m uvicorn app.server:app --port 8000 --reload > /tmp/uvicorn.log 2>&1 &

# 干净启动 uvicorn（不带 reload — 调试用）
cd backend
nohup ../.venv/Scripts/python.exe -m uvicorn app.server:app --port 8000 > /tmp/uvicorn.log 2>&1 &

# 看启动日志
sleep 8 && cat /tmp/uvicorn.log | head -20

# 测试 SSE
curl -N http://localhost:8000/rag/upload/<id>/stream
```

---

## 实战案例（2026-07-18）

| 场景 | 问题 | 修复 |
|------|------|------|
| 改 `_sse_encode` 加 `ensure_ascii=True` | 用户 curl 看到中文正常，但浏览器还显示 `文件` | **reload 没生效**。force kill + 干净启动后正常 |
| 改 `_do_index_sync` 加 duplicate 检测 | 改完后用户仍看到 `done` 不是 `duplicate` | reload 卡住新 worker。force kill 后正常 |
| 改 `upload_document` 加 `os.path.normpath` | 改完后 duplicate 检测 `existing=None` | 同上：reload 卡住。force kill 后正常 |

**结论**：每次 backend 代码改动后**必须**用 force kill 流程，不用 reload。

---

## 经验教训

1. **`--reload` 卡住是常态** — 特别在 `_do_index_sync` / `run_in_executor` 长任务时
2. **reload 没生效时不要怀疑代码** — 先验证 reload 成功
3. **多进程残留** — 旧 session 的进程可能没杀干净
4. **日志是真相** — `Started server process` 是 reload 成功的唯一标志
5. **调试时不用 reload** — 干净启动避免 reload 干扰