# 客服知识库种子文档（kb/）

本目录是客服域 RAG 知识库的**文档事实源**（入 git 可评审）。`scripts/index_cs_seed_docs.py`
会把这里的内容同步到 `{DOCS_DIRECTORY}/{kb_id}/customer/` 并索引进 RAG。

## 目录结构

- `cs_policy/` — 8 篇生产级政策文档（退款/价保/优惠券/积分/支付/配送/发票/售后），
  服务于用户对话的政策咨询（k_policy、k_faq 兜底、as_* 售后、k_promotion、t_delivery_estimate
  等意图的 kb_ids 均已声明 cs_policy，见 `backend/customer_service/router/intents.py`）。
- `cs_faq/` — 高频问题速查 + 历史 2 篇通用 FAQ（自 demo-kb 收编，逐字节一致）。
- `cs_product/`、`cs_complaint/`、`cs_scripts/`、`cs_aftersales/` — 历史 6 篇种子
  （自 demo-kb 收编，逐字节一致），与 `scripts/cs_kb_seed.py::SEED_FILES` 一一对应，
  由 `test_seed_files_all_exist_in_repo` 守卫（删文档必须同步删清单）。

## 纪律约定

1. **文档与 mock 行为互为事实源**：文档写 24h 人工审核，mock 就必须 24h 内可审；改其一必须同步另一个。
2. **数字规则一律标【业务确认】**：mock 环境无真实业务方拍板，生产上线前由业务逐项复核；
   清单见各文档内标注。
3. 每篇统一含：版本/生效日期/适用范围头部 + 「例外与升级路径（转人工）」结尾。
4. 与 `docs/customer-service/demo-kb/` 的关系：demo-kb 是早期模拟数据（含国际站多地区口径），
   仅作历史参考；本目录为现行生产口径，冲突时以本目录为准。

## 修改后如何生效

镜像内已含本目录（Dockerfile scoped COPY）。改文档后：

```bash
# 容器内重新执行种子索引（同步 + 增量索引）
docker exec agent-app-1 python scripts/index_cs_seed_docs.py
```
