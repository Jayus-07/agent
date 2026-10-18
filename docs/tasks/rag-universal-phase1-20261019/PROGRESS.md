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
