# 6a 数据集产品化 · 审计结论与设计定稿(2026-11-23)

> worktree 已建:agent-eval-batch6a / feat/eval-center-dataset-6a(基线 cef9fb4)
> 审计结论:拍板差距表(方案 §13.1)五项中**四项已满足**,真实缺口两项。

## 一、拍板表逐项复核(现状代码实证)

| 拍板差距 | 审计结论 | 证据 |
|---|---|---|
| 上传(格式单一→Excel/CSV) | **已满足**:CSV(含列映射 csv_mapping)/JSONL 全支持,5MB 上限,逐行校验 | `backend/evaluation/dataset/imports.py validate_upload`;前端 ImportWizard 已有模板下载/校验预览/跳错 |
| 用例编辑(无 UI) | **已满足**:待确认候选审核+已发布用例表格编辑,保存派生新版本(semver 次版本+1) | `DatasetsTab.tsx` VersionRow/rowSnapshot/parseExportRows |
| AI 生成进审核流 | **已满足**:GeneratePanel 冻结 KB 快照,产出进"待确认候选","核对来源/参考答案/证据后批准" | `DatasetsTab.tsx:307` 抽屉文案+924-929 完成态引导 |
| 审核 | **已满足**:反馈/Trace/生成三来源候选池+批准流 | 候选列表 sourceFilter 三态 |
| 版本冻结 | **已满足**:"已发布版本不可变,编辑生成新版本"+版本历史(发布/对比/回退/导出) | `DatasetsTab.tsx:218,57`;版本视图 |
| Golden 标注(无) | **真缺口**:无逐题 golden 标记,API 层 label 是 import 级标签非 golden | 全文 grep 无 golden |
| 双轨收敛 | **部分**:文件套件只读展示+V2 工作台并列,权重未定 | FileSuitesPanel vs "评测数据集"区块 |

## 二、Golden 标注设计定稿(待拍板后实施)

**红线实证**:`revision_content = {input, expected, evidence, metadata, source_ref}` 全部进 content_hash(repository.py:1466-1471)→ **直接 UPDATE 已发布 revision 的 metadata_json = 篡改 content_hash,违反"已发布不可变"**。

**方案(推荐):独立标注表,不触 revision 数据**

```sql
-- 迁移(一张表,可回滚 drop)
CREATE TABLE evaluation_v2.eval_case_golden (
  tenant_id     uuid/text NOT NULL,
  case_id       uuid NOT NULL,
  golden        boolean NOT NULL,
  reason        text NOT NULL DEFAULT '',
  marked_by     text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, case_id, created_at)  -- append-only 留历史
);
```

- repository:`set_case_golden`(幂等 append;重复同值返回现状)+`latest_golden_map`(批量取各 case 最新标注)
- 端点:`POST /cases/{id}/golden`(admin)+cases 列表响应带 `golden` 字段(JOIN 最新标注)
- 前端:已发布用例表格 Golden 徽标+切换按钮+「仅看 Golden」筛选;候选审核通过后可一键标 golden
- 语义:标注=治理行为(谁/何时/为何),与用例内容修订分离;发布锁版不受影响

**备选(否决)**:metadata_json 直改(破 hash 红线);revision 候选承载标注(一个布尔走全审核流,过重)

## 三、双轨收敛(本轮同 PR 的前端部分)

- 数据集页文案诚实化:文件套件区块标"仓库治理题集(权威主轨)";V2 区块标"数据集工作台(候选审核/编辑/版本管理)"
- 页面说明补双轨关系一句话;不做结构重排(编辑/发布流全挂 V2,重排是大工程另立项)

## 四、实施清单(下一轮)

1. 迁移脚本 + repository 两函数 + 端点 + 后端测试 ✅(见五)
2. 前端 golden 列/切换/筛选 + 双轨文案(下一批,同 worktree)
3. vitest + pytest 定向;PR;部署验收:生产实机标一条 golden→筛选→取消

## 五、实施记录(2026-11-23 第一批:后端数据面)

- 迁移 153_eval_case_golden.sql(append-only + RLS 同 V2 口径 + FK 到 eval_cases)
- repository:set_case_golden(幂等 append / 状态门槛 approved|published / 归档与缺失拒标)+ latest_golden_map(批量取最新标注,UUID 清洗,空输入短路)
- 端点:POST /v2/cases/{id}/golden(admin);GET latest-revision 响应合并 golden 字段
- 测试:test_case_golden.py 7 项全过(首标/幂等同值/来回切换/缺失归档拒/非法 UUID/映射清洗/空输入短路);archive 7 项无回归;api_contract 37 项全过
- **下一批(同 worktree)**:前端 golden 列/切换/筛选(表格数据源=exportV2DatasetVersion CSV,需导出链路带 golden 或前端二次拉取);双轨文案诚实化
