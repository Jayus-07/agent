# 客服 full_path 评测事实 ID 与 test KB 接线

## 目标

让客服 `full_path` suite 从生产 SSE/客服图路径读取受控 `cs_eval_kb` 合成知识，并为检索与沙箱订单结果输出可验证、稳定的事实 ID。只有在服务器内部评测上下文中允许读取 `audience=test` 知识库；普通客服请求仍受 customer 授权约束。

## 范围

- 将 suite 的 `kb_id`、`fixture_set`、数据集版本作为 full_path 运行范围；对不支持的评测 KB/语料范围 fail-closed。
- 为客服测试知识库建立独立 fixture manifest/索引入口，不扩展生产客服知识库 seeder。
- 用仅服务器内部可安装的评测上下文固定 `cs_eval_kb`；验证注册表仍标记 `audience=test`，传入 super_admin 检索身份和显式 KB 范围，关闭评测记忆写入，并绕过生产 FAQ 快捷回答。
- 从被选 fixture 的稳定文档 ID、章节来源和沙箱结构化订单事实导出 `fact_ids`；普通 `doc_id`、业务服务数据或自然语言文本不得伪装成事实 ID。
- RAG 事实来源必须保留回答实际引用的 chunk/章节，即使多个章节来自同一文件；不得用按文件去重的 SourceCard 代替。
- 标准后端镜像包含只读的合成 fixture 来源文件，使运行时校验与显式导入器使用同一锁版语料。
- 观察器与 full_path runner 消费显式事实 ID；补充隔离、错误和成功路径定向测试，并把新测试加进 RAG Smoke 免费 PR 清单。
- 更新客服评测现行文档及本任务进度。

## 验收

- 客服 full_path 只对受支持且声明 `cs_eval_kb` 的 suite 启用测试 KB；非法 KB、fixture、受众标签 fail-closed。
- 普通客服上下文仍使用 customer 身份、生产 FAQ 行为及原 KB 授权，不受评测开关影响。
- fixture 来源与章节映射完整、稳定；未知/缺失来源不产出事实 ID。订单事实仅从沙箱服务实际返回的结构化字段产生。
- 同一文件多个被引用章节均保留；RAG 本地与远端 `AskOutcome` 传递精简 citation evidence，公开 `answer_meta` 不暴露此内部字段。
- 非 suite 数据集仅携带 `dataset_version` 时继续走原 full_path 兼容路径，不启用 test KB。
- 相关 T0/T1 离线测试、静态检查通过；不调用真实模型/Provider。PR CI 与合并后免费检查通过。
- 本机 Docker rebuild/健康检查若环境不具备，明确标为 `UNVERIFIED`，不称部署完成。

## 不在本任务内

- `forbidden_facts` 的语义 Judge 与人工校准。
- 调用真实模型发布客服质量分数；本轮遵守离线限制。
- 改写客服业务行为、放宽 customer 授权或将测试 fixture 纳入自动生产部署。

## 当前状态

`COMPLETE`（代码与离线契约验收范围）。PR [#350](https://github.com/Jayus-07/agent/pull/350) 已以 squash merge 合入，merge SHA `6240f556096a92969c3b86f657902bf4ebaeaa93`；分支 head `6e776e057668f5b6e00768f13cbe35003d0e47ff`。离线测试与 PR CI 通过；fixture 实际导入/检索、Live 质量及 Docker 部署健康仍为 `UNVERIFIED`，由企业总任务后续授权和环境就绪后验收。
