# RAG文档测试集

## 文件放在哪里

- `backend/evaluation/fixtures/rag_eval_kb/files/`：28份正式上传样本，按16类场景分目录。
- `backend/evaluation/fixtures/rag_eval_kb/manifest.json`：正式样本清单、固定样本ID与SHA256。
- `backend/evaluation/datasets/rag/cases.jsonl`：24道事实题，含答案和来源证据。
- `backend/evaluation/datasets/rag/manifest.json`：题库版本与指纹。
- `backend/evaluation/datasets/rag/suites/`：8题快速组`desktop_upload_smoke`、24题完整题组`desktop_upload_full`。
- `backend/evaluation/datasets/rag/snapshots/baseline.json`：对应索引版本。
- `上传验收规则.json`：16类文件及6类上传操作的验收要求。
- `辅助样本/`：2份HTML来源、OCR文字对照PDF、故意损坏的Excel。仅用于核对，不在正式上传清单中。

正式样本、辅助样本各保留一份，内容未改变。旧草稿、重复副本、缓存、生成脚本和临时验证文件已移除。

## 当前状态

候选版本：`6.0.0-desktop-candidate.1`。此前候选包在 `b434138` 上验证过原读取代码；本次仓库基于 `2cd65eb`。本次接入仓库 RAG canonical、两组 suites、baseline snapshot 和 fixture manifest，管理端默认组与 CI 选择项已同步。运行 `backend.evaluation.preflight` 可校验题库、suite hash、28 份 fixture 路径与 snapshot；这不代表完成真实上传、检索、OCR、页面操作或付费调用。

请在独立评测环境使用整套清单、题库、源文件与快照重新建立索引；不要混用旧 baseline、旧 suite 或旧数据库。保留项目原有 Python 代码，不要把本目录当成整个 `backend` 覆盖。

管理端和 CI 共用本版本，选择 `desktop_upload_smoke` 或 `desktop_upload_full`。真实上传的 doc_id/chunk_id 以当次结果对应，不在题目中写死。评分及质量门槛沿用项目现有实现。

24题是当前完整题库，不代表已经覆盖16类文件的全部问答或原269题的业务范围。上传操作规则仍需接执行器；跨页表格完整行基线、性能门槛及部分产品策略仍在规则中明确列为待确认，不能算已通过。OCR需使用项目既有扫描件入口并通过其预检。
