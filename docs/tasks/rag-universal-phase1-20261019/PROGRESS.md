# 进展与续工

## 已核实

- GitHub main仍为b434138；共享工作区main为0828e58且有用户原有修改，未覆盖。
- 内置工具创建工作树成功，聊天注册因thread not found失败；attach重试也失败，继续使用返回的独立目录，未创建第二工作树。
- 现有`agent_memory`同时有客服、旅游、企业制度及测试KB，不能整库删除。只读盘点时`rag_eval_kb`有15份Active、1份pending_review，chroma169条、doc_db16条；未执行任何删除。
- 管理端真实页面缺少测试库选项；不是据此直接修改权限：原规则仅super_admin可访问test受众KB。
- 用户指定桌面临时凭据文件后，实际登录local_super_admin成功；只读确认其role=super_admin、must_change_password=true。业务接口403来自首次改密门禁。
- 管理端原登录页没有消费mustChangePassword，也没有改密页面，导致用户进入工作台后看到空列表。后端保护本身没有被绕过。

## 已实施的最小前端修复

- 沿用门户既有改密页和后端`/auth/change-password`接口，在管理端补入口。
- 临时密码登录跳到改密页；路由守卫阻止待改密用户加载其他业务页。
- 改密成功换发令牌，合并后端部分userInfo摘要，保留角色/租户；失败不清改密标记。
- 上传文件选择补CSV，并让提示列出已支持的XLSX/CSV；后端原本已登记两者，不改变上传权限。
- 未更改任何真实密码、must_change_password字段、账号角色、生产Prompt或Provider配置。

## 实际验证

- Vitest定向3文件11项通过：认证会话、改密成功/失败、待改密路由及原有RoleGate。
- 管理端TypeScript `--noEmit --pretty false --incremental false`退出0；git diff --check通过。
- 实际浏览器在修复版`http://localhost:3201/login`登录临时账号后跳到`/change-password`，新密码字段未填、未提交，已交给用户操作。
- 截图：`D:/tmp/rag-universal-phase1-20261019/first-password-change.png`。
- 复用现有Next15.5.28依赖；跨盘Junction导致Next编译失败，改为复制已缓存依赖到本工作树后HTTP200并实际浏览器通过，未重新下载。
- 曾尝试门户3000辅助入口，因旧缓存Next14与跨盘依赖问题失败，已停止该进程。临时管理端3201运行于exec session74845，保留供用户改密；其服务端API Key仅在进程环境，未输出。
- Next自动修改的frontend/tsconfig已恢复；frontend-admin/tsconfig自动生成的临时类型目录变更不纳入功能提交，预览停止后恢复。

## 免费解析/过滤证据

执行当前真实parse_and_chunk_full、_filter_index_chunks、apply_index_chunk_identity及质量审计；禁用网络外呼，未调用Provider、未连接业务库写数据、未生成向量。

| 文件 | 过滤后Leaf | Parent | 覆盖缺口/硬异常/悬空引用 |
| --- | --- | --- | --- |
| faq_only.docx | 40 | 1 | 均0 |
| preamble.docx | 6 | 4 | 均0 |
| contract_table.docx | 4 | 1 | 均0 |
| sku.csv | 501 | 1 | 均0 |
| multi_sheet.xlsx | 89 | 4 | 均0 |

Artifact：`D:/tmp/rag-universal-phase1-20261019/free-inspection.json`、`free-postfilter.json`及对应chunk明细。这里只验证免费切分/过滤，不能当作真实上传、模型元数据和向量发布通过。

## 2026-10-19 隔离上传栈建成（ragu1）

- 新增 `部署/rag-universal-phase1-20261019/docker-compose.isolated.yml`（compose 项目 `ragu1`）：独立网络/卷/容器（ragu1-*），服务=postgres(pgvector)+redis×3(缓存/认证/broker)+db-migrate+app+rag-service+rag-index-worker+apisix；网关 127.0.0.1:19080、PG 对账通道 127.0.0.1:15433；worker 并发 1+2GiB（同 rag-t2 档）。不写 container_name、不与共享栈/PR#52 栈冲突。
- 镜像 `agent-platform:ragu1-0423d69`：完整构建被 Clash fake-ip 截断（198.18.0.13/2001:2::e 对 mirrors.aliyun.com apt 大文件 500/502，宿主 curl 小响应 200 但 build 容器断），改用 `Dockerfile.overlay` 在 agent-app:latest（2026-10-13 构建，依赖声明 2026-10-10 后未变）上叠加最新代码层，GIT_COMMIT=ragu1-0423d69 注入。
- 运行代码版本证据：`GET :19080/health` → `{"status":"ok","build":{"commit":"ragu1-0423d69"}}`，迁移 applied=101；pipeline.py/filter.py/indexer.py 三文件 host=app=worker SHA256 一致。
- 隔离库初始化：init_db 全量迁移 rc=0；`init_clone_config.py`（D:\tmp\rag-universal-phase1-20261019\）复制配置面：账号 local_super_admin（哈希=用户改密后，role=super_admin/tenant=default/must_change_password=false）、llm_providers 12、凭据密文 10、llm_models 20、角色绑定 7、专项绑定 2、model_price 239（approved 123）、预算 8、prompts 74+aliases 41+runtime_state 1、keyword_rules 133、metadata 规则 133。**不复制任何业务/运行数据**：doc_registry/chunk_store/rag_vectors/rag_index_runs 全 0。
- 已知差异（记录不修复）：共享库 prompt_release_records 有 b434138 迁移没有的 model_snapshot 列（结构漂移，审计表非运行必需）未复制；sys_config 由服务自建幂等表（JWT_SESSION_GUARD_MODE 回退 env=enforce 等价）；schema_consistency 报 tasks/checkpoints 缺失属惰性建表。
- 付费前预检七项全绿：①manifest SHA256 五份源文件一致（UF-03-01/05-01/08-01/11-01/12-01）；②rag_eval_kb audience=test 仅 super_admin；③角色=embedding qwen3.7-text-embedding / rerank qwen3.7-text-rerank / chat doubao-seed-2.0-mini，approved 价格齐（embedding/rerank 0.5 CNY/1M tokens）；④预算 tenant_default|default 432 CNY/日 hard，worker bind_request_budget(upload_id) 归因；⑤隔离库 RAG 四表全 0；⑥PARSABLE_EXTS 含 docx/csv/xlsx；⑦API/Worker 代码指纹一致。网关无凭据 401（fail-closed），LLMRegistry 在 app+worker 均注入 10 份凭据（Fernet 解密成功）。
- 管理端 3201 已重启（`D:\tmp\rag-phase1-admin-isolated.py`），BFF 指向 19080 隔离网关；HTTP 200。旧 3201（指向共享 9080）进程已停。
- 等待：用户在 127.0.0.1:3201 登录（账号 local_super_admin，密码=自己改密后的新密码；隔离库哈希与共享库同源）。登录后进知识库工作台上传首批 5 份（faq_only.docx/preamble.docx/contract_table.docx/sku.csv/multi_sheet.xlsx，选「统一 RAG 评测知识库」）。

## 当前等待与下一步

- 用户在3201完成首次改密后再继续浏览器；新密码不必提供给助手。旧3200尚未部署修复。
- 先确认实际运行后端代码版本及测试KB/租户隔离，再准备可恢复清理和小批上传；此前没有删除旧库或产生本轮模型费用。
- 第一阶段正式数据集注册、浏览器上传、向量/元数据质量检查尚未完成；不宣称测试集接入或RAG验收完成。

## 运行代码漂移（付费前必须处理）

- 现有app镜像未挂载源码。对三份运行文件做LF归一化SHA256后，与b434138的pipeline/filter/indexer均不一致。
- 运行中pipeline没有`structured=True`清洗调用，indexer没有`cleaner_content_loss`付费前检查；确认没有当前main的#90关键保护，不是单纯行尾不同。
- 不直接向这套旧后端付费上传来冒充当前main验收。后续需在隔离栈运行最新代码、关联同版本Worker，再通过浏览器上传。
- 用户反馈管理端没有改密提示后，已实际复现并修复上述前端闭环；修复页截图已提供，等待用户亲自设置新密码。
- 上传CSV选择改动尚未在实际文件选择器中验收；不能把这两行源码修改报告为浏览器上传通过。
