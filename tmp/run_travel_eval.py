# -*- coding: utf-8 -*-
"""Phase 5 验收（临时脚本）：走 EvaluationService 完整链路跑 travel 模块。"""
import sys

sys.path.insert(0, r"D:\Program Files\workplace\agent")

from backend.evaluation.config import EvalConfig
from backend.evaluation.service import EvaluationService

svc = EvaluationService()
try:
    config = EvalConfig(module="travel")
except TypeError:
    # EvalConfig 可能是 dataclass/pydantic 且字段名不同——打印帮助定位
    import inspect
    print(inspect.signature(EvalConfig))
    raise

report = svc.evaluate(config)
for s in report.summaries:
    print("module=%s total=%d passed=%d failed=%d errors=%d pass_rate=%.3f" % (
        s.module, s.total, s.passed, s.failed, s.errors, s.pass_rate))
    for k, v in (s.metrics or {}).items():
        print("  metric %s=%s" % (k, v))
print("tier_summaries:")
for t in report.tier_summaries or []:
    print("  tier=%s total=%d passed=%d failed=%d" % (
        t.tier, t.total, t.passed, t.failed))
bad = [r for r in report.results if r.status != "pass"]
for r in bad:
    print("NOT PASS: %s -> %s (%s)" % (r.case_id, r.status, (r.error_msg or "")[:80]))
print("REPORT_OK" if not bad else "REPORT_FAILED")
sys.exit(0 if not bad else 1)
