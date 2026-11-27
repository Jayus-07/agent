# 任务规划：Tier 7 - backend/app/api/routes/rag_upload.py 模块化解耦重构

## 1. 目标与背景
`backend/app/api/routes/rag_upload.py` 当前为 2681 行的超大文件，作为知识库文件上传、转码、分块与索引调度的核心 API 路由，承担了高度重叠的异构职责：
1. **多格式文件上传与预检 (File Upload & Preflight)**：单文件/多文件/目录上传、文件指纹校验、格式探测、大小与魔数过滤；
2. **压缩包解压与归档展开 (Archive Extraction)**：Zip / Tar 等压缩包解包、嵌套路径遍历与临时清理；
3. **格式转码与预处理 (Parsing & Transcoding)**：文本抽取、OCR/图片预处理分发、Markdown 规范化；
4. **切分与索引调度编排 (Chunking & Index Pipeline Scheduling)**：异步任务下发、Celery/进程池调度、Pipeline 参数组装；
5. **SSE 实时状态推送 (SSE Event Stream)**：进度通知、阶段变更事件流与心跳保活；
6. **公共契约与工具依赖 (Schemas & Helper Utilities)**：Pydantic 请求体/响应体 Schema、权限校验与临时路径管理。

本任务依据马斯克五步骤（质疑需求 -> 删除多余 -> 简化优化 -> 加快周期 -> 自动化）与单一职责原则（SRP），将该超大路由解耦为专职子路由领域包 `backend/app/api/routes/rag_upload_subroutes/`，门面 `rag_upload.py` 瘦身至纯 Router 聚合入口（控制在 150 行以内），同时 100% 保持对外 HTTP API 路由契约、参数结构及测试 Monkeypatch 的兼容性。

## 2. 改造范围
- 目标文件：`backend/app/api/routes/rag_upload.py`
- 重构后结构：
  - `backend/app/api/routes/rag_upload_subroutes/` (专职子领域路由包)
    - `base.py`: 动态门面透明代理 `FacadeProxy`、公共常量与日志实例
    - `schemas.py`: 所有 Pydantic 请求/响应契约模型与枚举
    - `deps.py`: 权限校验、租户解析、临时目录分配与通用依赖辅助
    - `upload.py`: 核心文件上传与预检端点
    - `batch.py`: 批量文件与压缩包解压上传端点
    - `stream.py`: SSE 进度事件流与状态轮询端点
    - `__init__.py`: 聚合子路由并构建统一主路由器
  - `backend/app/api/routes/rag_upload.py`: 门面 Router，继续导出 `router = APIRouter(...)`，并重新导出全部公开符号与函数
- 架构守卫测试：
  - `backend/tests/api/test_rag_upload_modular_architecture.py`

## 3. 验收标准
1. `backend/app/api/routes/rag_upload.py` 单文件行数由 2681 行瘦身至 200 行以内（>90% 降幅）；
2. FastAPI 路由表路径、HTTP 方法、参数结构与状态码 100% 保持不变；
3. 全仓现有涉及 `rag_upload` 的测试全部通过（包含已有的 monkeypatch 场景）；
4. 架构守卫测试通过并防止职责重新混杂；
5. 新建 PR 并通过全套 CI 门禁合入 main。
