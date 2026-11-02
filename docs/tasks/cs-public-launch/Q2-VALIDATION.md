# Q2 知识链路冒烟（2026-11-02）

结论：知识链路本轮验收指标通过，整体质量门禁仍不通过；不据此宣称公开投产。共享 BM25 无需补建，宿主执行环境缺陷已由专用入口修正。

## 根因与改动

宿主默认 RAG_MODE=local，读取本工作树不存在的 backend/data/bm25；生产 app 使用 remote，共享索引已发布且 ready。只读检索 cs_faq 返回 548 字符、index_status=ok。原“生产索引缺失”判断不能沿用。宿主 BUSINESS_SERVICE_URL 还指向 Docker 名称 business-service，诊断批次查询发生 DNS 失败。

scripts/run_cs_live.py 显式使用本机 remote RAG :8090、业务服务 :8081、NO_PROXY=*、退款申请 disabled；检查 8088 模型与服务就绪、刷新模型注册表后调用原 CLI。共享容器、索引和 .env 均未修改。

复跑命令（仓库根）：

```powershell
& 'D:/Program Files/workplace/agent/.venv/Scripts/python.exe' scripts/run_cs_live.py --selection cs_public_launch_smoke --run-id <新的运行ID>
```

命名清单仅引用锁版 case ID、校验 canonical hash。v1 全部20 + CS2 faq12/query8/action8/complaint4/safety4/multi_turn4；本次运行使用临时 JSONL，与命名清单 case ID、题面和期望一致，未改锁版题面或阈值。

## 实际结果

| 指标 | 诊断批 cs-prod-q2-1102 | 修正后 cs-prod-q2-1102-r2 |
|---|---:|---:|
| 样本 | 60 | 60 |
| pass | 38 | 46 |
| 执行 error | 0 | 0 |
| 答案含“系统繁忙” | 0 | 0 |
| v1 pass | 9/20 | 9/20 |
| DNS 缺陷 | 存在 | 未见 |

修正后8条 query 全部恢复，无 pass→fail。v1 与 P6 的9/20一致；离线路由回放门禁6/6通过。≥60条与繁忙率≤5%要求达成，但 legacy CLI 仍因 smoke50%<95%、core80.8%<85%退出1；这是质量门禁失败，不伪造为完整质量PASS。Q3需处理既有标注/行为分歧、多轮与断言范围。

T0 py_compile、diff --check PASS；入口+workflow契约8 passed；runner相邻回归8 passed；决策回放6 passed。新入口是宿主脚本，不改变服务运行代码，无受影响后端服务需要重建。

## 成本与限制

复测窗口 01:38:40–01:41:00 UTC，llm_usage 全库90行、47296 tokens、cost_usd合计0.00278（仅成本字段USD口径，包含并行任务，作为窗口上界参考，不是精确run对账）。run request_id下零行；远端使用rag-ask:*、部分调用空身份，且有子线程预算上下文缺失警告。未声称预算预占/跨线程归因有效；Q3预算频次决策必须计入此限制。

本轮使用现有合成eval_bot，动作/投诉可能新增测试会话工单；资金申请在入口强制disabled。未擅自删除关联记录。共享服务可能被并行会话重建，首次预探超时后待ready恢复成功；正式复测未改共享服务。

原始报告：data/eval_runs/cs-prod-q2-1102-r2/report.json（gitignore）；SHA256 cd54c077a0167851422b1af8cf528e8a598bbceff00b0506072086d3318dae5d。日志d:/tmp/cs-q2-smoke-r2.log。Q2 PR/CI完成后才标记阶段完成；Q1共享管理端部署协调项、Q3–Q6仍未完成。
