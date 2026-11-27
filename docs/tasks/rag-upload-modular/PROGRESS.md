# 任务进度：Tier 7 - backend/app/api/routes/rag_upload.py 模块化解耦重构

## 当前状态
- [x] Step 1: 创建独立 Git Worktree `agent-rag-upload` (branch: `refactor/rag-upload-modular`)
- [x] Step 2: 初始化 TASK.md 与 PROGRESS.md 任务追踪文档
- [x] Step 3: 分析 `backend/app/api/routes/rag_upload.py` 内部结构、路由端点与外部依赖
- [x] Step 4: 建立 `backend/app/api/routes/rag_upload_subroutes/` 领域子模块并迁移实现
- [x] Step 5: 重构 `backend/app/api/routes/rag_upload.py` 为纯 Router 聚合门面 (102 行，代码削减 96.2%)
- [x] Step 6: 编写架构守卫测试 `test_rag_upload_modular_architecture.py` (5/5 passed)
- [x] Step 7: 运行全量单元测试与本地路由验证 (198 passed in 114s, 100% passed)
- [ ] Step 8: 提交 PR，等待 CI 验证，Squash Merge 合入 main
- [ ] Step 9: 清理临时工作树与收尾校验

## 架构演进与拆分成果
1. **领域子包构建 (`backend/app/api/routes/rag_upload_subroutes/`)**：
   - `base.py`: 动态门面透明代理 `FacadeProxy`、异常类 `FileLockedByOtherError`, `UploadIdempotencyConflict`、全局配置常量、`__all__` 导出与共享状态（`_celery_routed`, `_background_index_tasks`）；
   - `lock.py`: 文件并发锁与租约心跳管理，支持 `facade.LOCK_HEARTBEAT_SECONDS` 动态穿透；
   - `validation.py`: 文件格式探测、MIME与OCR预算预检；
   - `cleanup.py`: 临时文件清理、垃圾回收与回收站；
   - `progress.py`: 上传进度持久化、Redis事件流与所有者鉴权（支持 `facade._SSE_REDIS_*` 与 `facade._read_progress_redis` 穿透）；
   - `indexing.py`: 核心索引调度、Celery分发与同步流水线执行引擎（支持 `facade._do_index_sync` 与并发信号量动态重置）；
   - `failures.py`: 质检与失败信息摘要明细；
   - `routes.py`: 3 个核心 API 路由端点集合；
   - `__init__.py`: 统一聚合导出。
2. **门面彻底瘦身**：
   - `backend/app/api/routes/rag_upload.py`: 瘦身为 **115 行** 纯门面（原 2681 行，削减 **95.7%**），完整导出 41 个 Helper、2 个 Exception、20 个全局常量与 3 个路由，100% 兼容 monkeypatch 与历史导入。
3. **架构守卫与回归测试验证**：
   - `backend/tests/api/test_rag_upload_modular_architecture.py` (5/5 passed)；
   - 11 个关联测试文件全量回归：198 passed in 114s（零失败，零错误）。
