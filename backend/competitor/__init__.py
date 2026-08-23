"""
competitor — 竞品分析模块（最小闭环 V1）

链路: 竞品 URL → web_crawl 抓取 → 平台适配/LLM 结构化抽取 → 快照入库 → 历史对比

目录:
  store.py      — SQLite 存储（watchlist 监控配置 + snapshots 抓取快照）
  adapters.py   — 平台识别 + 规则抽取（正则，LLM 不可用时的兜底）
  extractor.py  — LLM 结构化抽取（JSON schema 约束 + 失败降级规则抽取）
  pipeline.py   — 抓取→抽取→入库→对比 分析管线
"""
from backend.competitor.store import CompetitorStore, get_store, reset_store
from backend.competitor.pipeline import analyze_url, scan_watchlist, history_report

__all__ = [
    "CompetitorStore",
    "get_store",
    "reset_store",
    "analyze_url",
    "scan_watchlist",
    "history_report",
]
