# 启动 / 测试命令

## 后端启动

```bash
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.server:app --reload --host 127.0.0.1 --port 8000
```

> **注意：** `--reload` 只监听已存在文件的修改。新增 Python 文件/路由后必须手动重启，否则 404。

## 前端启动

```bash
cd frontend
npm run dev
```

## 类型检查

```bash
cd frontend
npx tsc --noEmit
```

## 测试

```bash
# 前端单次
cd frontend && npm test

# 前端 watch
cd frontend && npm run test:watch

# 前端覆盖率
cd frontend && npm run test:coverage

# 后端
cd backend && pytest
```

## URL

- API 文档：http://localhost:8000/docs
- 前端：http://localhost:3000
- MCP 工具：http://localhost:8000/mcp/tools

## 一键脚本

- 启动：`start_all.bat`
- 停止：`stop_all.bat`
- 重启：`restart_all.bat`