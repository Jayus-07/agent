"""元数据基线评估脚手架（规划阶段 1.2 / 1.3 / 2.2）。

对黄金标注集分别跑「规则链」与「统一抽取（含级联路由）」两条链路的
doc_type / domain 预测，输出 P/R/F1（per-label + macro）、混淆矩阵、
延迟与风险字段召回，作为上线门禁（成功标准 ≥ 基线 + 5pp）的测量工具。

用法（仓库根，py 解释器为项目 venv）：
  # 1) 规则链基线（实时推理；胶着样本会触发既有 LLM 仲裁，属规则链真实成本）
  python -m backend.eval.metadata_baseline.evaluate \
      --golden backend/eval/metadata_baseline/golden.jsonl --pred rule

  # 2) 统一抽取 / 级联路由：先离线生成预测文件（一次 LLM 成本，可反复回放评估）
  python -m backend.eval.metadata_baseline.predict \
      --golden backend/eval/metadata_baseline/golden.jsonl \
      --out backend/eval/metadata_baseline/preds_unified.jsonl [--cascade]
  python -m backend.eval.metadata_baseline.evaluate \
      --golden backend/eval/metadata_baseline/golden.jsonl \
      --pred backend/eval/metadata_baseline/preds_unified.jsonl

  # 3) 输出 JSON 报告（含混淆矩阵）
  ... --json report_unified.json

黄金集 JSONL 格式见本目录 README.md；标注数据本体不入库（外部阻断项，
规划 §7.2），由标注 PM 提供。
"""
