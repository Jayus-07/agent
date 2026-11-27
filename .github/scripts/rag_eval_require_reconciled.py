"""检查评估账本 summary 是否达标（用于 Require reconciled cost ledger 步骤）."""

import json
from pathlib import Path
import sys


def main() -> None:
    summary_path = Path("data/eval_runs/llm_usage_summary.json")
    if not summary_path.exists():
        sys.exit("未找到 llm_usage_summary.json 对账报告，评测禁止通过")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("cost_verification") not in {"VERIFIED", "ESTIMATED"}:
        sys.exit("费用未定价或无法验证，禁止通过真实评测")
    if summary.get("budget_ledger_verification") != "VERIFIED":
        sys.exit("身份或预占/结算账本未完成对账，禁止通过真实评测")
    if summary.get("budget_envelope_verification") != "WITHIN_LIMITS":
        sys.exit("整次评测费用预算超限或无法核实，禁止通过")
    print("[verify] 费用定价、账本对账与预算限额全部验证通过")


if __name__ == "__main__":
    main()
