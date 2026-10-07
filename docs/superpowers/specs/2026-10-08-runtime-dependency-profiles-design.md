# 运行时依赖分层设计

**日期：** 2026-10-08
**状态：** 设计已获用户批准，待用户审阅
**范围：** 生产后端镜像依赖分组；保留旅游助手、客服助手、文档检索与 RAGAS 评测能力。

## 1. 目标

在不关闭用户可见能力、不触碰客服/旅游在途改动的前提下，减少生产服务不需要的 Python 运行时依赖和构建下载。当前部署运行旅游 AI 助手、客服助手，同时保留管理端评测入口；RAGAS 不得移除。

设计必须让服务明确声明需要的依赖 profile，避免一个通用镜像把评测、OCR、本地模型和网页抓取依赖无差别装进所有服务。依赖被移出某个镜像不等于全局删除对应功能：需要该功能的服务仍通过 profile 安装。

## 2. 当前事实

- 根 `Dockerfile` 目前执行 `pip install -e ".[postgres,ragas]"`；Docker Compose 的多个代码服务使用统一 `x-build`。
- `/api/evaluation` 的在线评测在 `app` 进程中调用 `EvaluationService`，可选择 RAGAS；因此 `app` 镜像必须保留 `ragas`。
- `backend/evaluation/ragas_bridge.py` 通过 `backend.rag.embedding_singleton.get_embedding()` 复用当前 Embedding。云端绑定时不必仅因 RAGAS 加载本地 HuggingFace 模型；本地绑定时则需要本地模型依赖。
- `sentence-transformers` / `langchain-huggingface` 用于本地 Rerank / Embedding 路径。项目默认模式是 cloud，但管理端角色绑定支持动态配置；不得在未审查热切换契约前，让仍承诺支持 local 的服务缺少依赖。
- `crawl4ai` 由 `backend/tools/crawler_runtime.py` 延迟导入，仅网页抓取能力需要。
- `chromadb` 已不是生产 RAG 检索存储；代码注释表明其仅供残留数据清理脚本和兼容性测试。该脚本所需依赖应留在维护/工具 profile。
- 当前生产后端安装命令没有选择已有的 `ocr` extra；而 OCR 配置默认选择 RapidOCR。扫描件 OCR 是否可用必须由后续服务级验证确认，不能把“未安装”误当作已支持。
- `modelscope` 未发现后端生产代码直接导入。若迁移到维护/模型下载 profile，需同时检查仓库外运维脚本是否依赖它。
- 文档上传/RAG 解析需要的 PDF、DOCX、XLSX 解析包，以及 PostgreSQL、Redis/Celery、LangGraph、现有模型 provider 客户端，不因本次瘦身而删除。

## 3. 设计

### 3.1 依赖组

在 `pyproject.toml` 中保留一个可运行旅游/客服助手的核心集合，并把大体积或仅专项功能使用的包归入显式 extras：

| Profile | 依赖/用途 | 安装边界 |
|---|---|---|
| `postgres` | psycopg v3（LangGraph checkpoint）及 psycopg2（存量同步 SQL） | 需要 PG 的服务；保持当前契约 |
| `ragas` | RAGAS 评测器及其传递依赖 | `app` 必须安装；其他服务只在其实际执行 RAGAS 时安装 |
| `local-models` | `sentence-transformers`、`langchain-huggingface` 及 CPU Torch 约束 | 仅安装在支持本地 Embedding/Rerank 的服务 |
| `crawler` | `crawl4ai` 及浏览器运行时所需包 | 安装在实际执行网页抓取 capability 的服务 |
| `ocr` | 已有 OCR extra（RapidOCR、OpenCV、ONNX Runtime） | 安装在实际解析扫描件的服务；若产品选择云端 OCR，则按该路径验证 |
| `maintenance` | `chromadb` 等仅维护脚本需要的工具依赖 | 不默认进入聊天服务镜像；执行相关维护脚本时显式安装 |
| `dev` | pytest、测试工具与测试报告工具 | 仅开发/CI 使用 |

`modelscope` 若仓库内外操作脚本审查确认需要，应放入单独的模型下载/维护 profile；否则从声明中移除。不得将其留在默认运行集合但又没有已知生产消费者。

具体 extras 名称可在实施时遵从已有命名风格，但其边界与上述职责不可合并回单一默认安装命令。

### 3.2 镜像与服务 profile

Dockerfile 增加显式、可审计的依赖 profile 构建参数；Compose 为服务声明稳定的 profile，而不是依赖开发者 shell 中的临时环境值。第一版服务映射按调用路径确认后落定，至少遵守：

- `app` 保留 `postgres` 和 `ragas`，确保在线评测中心和 Prompt 发布评测行为不变。
- 支持本地 Embedding/Rerank 热切换的服务必须带 `local-models`；只有经代码与生产绑定核验确认仅用云端的服务才可省略。
- 抓取 capability 的实际执行服务保留 `crawler`；未承载该执行路径的服务不安装。
- 文档入库链路按实际解析进程安装 PDF/DOCX/XLSX 解析依赖；扫描件 OCR 若作为生产承诺，则在对应解析服务安装 `ocr` 并纳入验收。
- 维护脚本需要的包通过维护 profile 提供，不要求常驻对话容器携带。
- 保留现有 provider 客户端与管理端可配置能力；本次不按“当前正在使用的单一模型”删掉其他已支持 provider。

缺少可选依赖时，相关可选能力必须返回明确的“该服务 profile 未启用”错误或状态，不得在模块导入期导致聊天服务崩溃，也不得把失败伪装成成功降级。

### 3.3 RAGAS 与本地模型解耦

保留 `ragas` extra 和 `app` 中的评测入口。RAGAS 使用已配置的 Embedding 角色：云端 Embedding 可以继续在线评测；若生产选择 local Embedding，则相应执行服务需安装 `local-models`。实施前要验证 `AnswerRelevancy` / `AnswerCorrectness` 等 embedding 指标在云端与本地两种配置下的实际依赖，不因 RAGAS 需要 Embedding 就默认把 Torch 装入所有服务。

## 4. 非目标与安全边界

- 不删除 RAGAS，不关闭评测中心或 Prompt 发布门禁。
- 不更改旅游/客服业务逻辑、模型绑定语义、工具注册或上传流程。
- 不触碰主工作区现有未提交客服/旅游文件；依赖变更应在独立分支实施。
- 不在当前 ECS 磁盘水位下启动构建，也不执行 Docker prune、删除镜像/卷或重启容器；空间回收另行确认。
- 不改变已支持的 provider、crawler、OCR 或 local-model 功能的用户可见契约；只能把依赖移至需要它的服务 profile。

## 5. 验收标准

1. 服务—capability—依赖 profile 对照表由真实导入/执行路径核验，不凭服务名称猜测。
2. 每个生产服务入口可在自己的最小 profile 中启动；所有代码服务的健康检查通过。
3. 旅游助手与客服助手完成在线对话及 RAG 检索冒烟，Embedding/Rerank 按实际云端绑定成功。
4. `app` 完成离线自研评测、在线 RAGAS 评测及 Prompt 发布评测路径；RAGAS 指标不会因 profile 缺失静默变成零分或假成功。
5. 文档上传至少覆盖文本 PDF、DOCX、XLSX；扫描件 OCR 若仍是产品承诺，需另有正向识别样例，否则配置必须显式标为关闭/云端路径。
6. 对启用的 crawler capability 做实际调用验证；未启用服务不能误暴露为可执行但运行时缺包。
7. 本地模型 profile 做 import 与最小推理测试；云端 profile 不导入 Torch，避免启动期开销。
8. 在有足够磁盘余量的环境串行构建；记录新旧镜像大小、依赖下载量与构建时间，并核对 Build Identity 指向目标提交。
9. 依赖锁、Dockerfile、Compose profile、CI 测试矩阵一致；全 extras 的 CI 覆盖不替代最小生产 profile 的启动/功能测试。

## 6. 实施顺序

1. 先生成服务—依赖调用图，尤其确认 RAGAS、local Embedding/Rerank、文件解析/OCR、crawler 分别由哪个服务执行。
2. 增加 extras 和显式镜像 profile；保留现有 provider 与功能入口。
3. 增加最小 profile 的服务启动及功能冒烟测试，并保留全 extras 测试覆盖。
4. 在非生产环境按服务串行构建并比较体积/缓存命中；修复 profile 漏项。
5. 确认服务器磁盘有安全余量、仅一个会话持有构建权后，再安排 ECS 构建与验收。

## 7. 实施前仍需核实

- 生产 DB 中 Embedding/Rerank 实际绑定，以及哪些进程必须支持热切换到 local。
- 文档上传和解析的真实 Celery 队列/执行容器，决定 `ocr` 与解析依赖的 profile 归属。
- crawler capability 的执行位置及是否仍是旅游/客服用户路径的支持能力。
- `modelscope` 是否被仓库外的运维/模型下载脚本使用。
- `requirements-lock.txt` 的生成来源、生产/CI 使用约定与 Dockerfile 实际安装清单是否一致，避免声明和实际依赖集合漂移。
