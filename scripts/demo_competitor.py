"""scripts/demo_competitor.py — 竞品分析功能演示脚本

演示最小闭环:
  1. analyze  — 抓取竞品商品页 → 抽取价格/库存/促销 → 快照入库
  2. add      — 加入监控列表（自动抓基线快照）
  3. analyze  — 再次分析（演示与上次快照的价格对比）
  4. history  — 价格历史报告
  5. list     — 监控列表

用法:
  cd <项目根目录>
  .venv/Scripts/python.exe scripts/demo_competitor.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.tools.competitor import competitor_analyze_tool  # noqa: E402

URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"


def section(title: str):
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


if __name__ == "__main__":
    section("1) 对话式分析竞品 URL（action=analyze）")
    print(competitor_analyze_tool.invoke({"action": "analyze", "url": URL, "question": "帮我分析这个竞品"}))

    section("2) 加入监控列表（action=add，自动抓基线快照）")
    print(competitor_analyze_tool.invoke({
        "action": "add", "url": URL, "name": "竞品A - A Light in the Attic",
    }))

    section("3) 再次分析（action=analyze，演示价格变化对比）")
    print(competitor_analyze_tool.invoke({
        "action": "analyze", "url": URL, "question": "这个竞品最近降价了吗",
    }))

    section("4) 价格历史（action=history）")
    print(competitor_analyze_tool.invoke({"action": "history", "url": URL}))

    section("5) 监控列表（action=list）")
    print(competitor_analyze_tool.invoke({"action": "list"}))

    print("\n演示完成。数据库: data/competitor.db")
