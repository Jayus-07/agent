"""
data_collection/pipeline.py — 数据采集主编排器

将 Fetcher → Parser → Cleaner → Analyzer → Writer 串联为一条流水线。
每个阶段产出强类型 dataclass，任一阶段失败即停止并返回错误。

用法:
    pipeline = CollectionPipeline(
        fetcher=StaticDataFetcher(),
        parser=JsonParser(),
        cleaner=DefaultCleaner(),
        analyzer=StatsAnalyzer(),
        writer=SQLAlchemyWriter("postgresql://..."),
    )
    result = pipeline.run(
        source="static://datasets/products.json",
        table="stg_products",
        dedup_keys=["SKU"],
    )
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from data_collection.fetchers.base import AbstractFetcher, RawData
from data_collection.parsers.base import AbstractParser, ParsedData
from data_collection.cleaners.base import AbstractCleaner, CleanedData
from data_collection.analyzers.base import AbstractAnalyzer, AnalyzedData
from data_collection.writers.base import AbstractWriter, WriteResult
from utils.logger import logger


@dataclass
class CollectResult:
    """单次采集任务完整产出 — 包含每个阶段的结果"""
    task_id: str
    source: str
    status: str = "pending"          # "success" | "partial" | "failed"
    raw: RawData | None = None
    parsed: ParsedData | None = None
    cleaned: CleanedData | None = None
    analyzed: AnalyzedData | None = None
    write: WriteResult | None = None
    elapsed_ms: float = 0.0
    error: str | None = None

    def to_markdown(self) -> str:
        """生成 Markdown 格式的采集报告（供 Reporter 使用）"""
        lines = [
            f"## 📥 数据采集报告",
            f"",
            f"| 项目 | 详情 |",
            f"|------|------|",
            f"| 任务ID | `{self.task_id}` |",
            f"| 数据源 | `{self.source}` |",
            f"| 状态 | **{self.status}** |",
            f"| 总耗时 | {self.elapsed_ms:.0f} ms |",
        ]

        if self.parsed:
            lines.append(f"| 解析记录 | {self.parsed.record_count} 条 |")

        if self.cleaned:
            lines.append(f"| 清洗后 | {self.cleaned.row_count} 条 |")
            if self.cleaned.dedup_removed:
                lines.append(f"| 去重移除 | {self.cleaned.dedup_removed} 条 |")

        if self.write:
            lines.append(f"| 写入 | {self.write.inserted} 条 |")
            if self.write.skipped:
                lines.append(f"| 跳过 | {self.write.skipped} 条 |")

        if self.error:
            lines.extend(["", f"⚠️ 错误: {self.error}"])

        return "\n".join(lines)


class CollectionPipeline:
    """数据采集主编排器

    将 Fetcher / Parser / Cleaner / Analyzer / Writer 串联执行。
    每阶段产出强类型 dataclass，失败立即返回。
    """

    def __init__(
        self,
        fetcher: AbstractFetcher,
        parser: AbstractParser,
        cleaner: AbstractCleaner,
        analyzer: AbstractAnalyzer | None = None,
        writer: AbstractWriter | None = None,
    ):
        """
        Args:
            fetcher: 数据获取器
            parser: 数据解析器
            cleaner: 数据清洗器
            analyzer: 数据分析器（可选，传 None 跳过分析）
            writer: 数据库写入器（可选，传 None 跳过写入）
        """
        self.fetcher = fetcher
        self.parser = parser
        self.cleaner = cleaner
        self.analyzer = analyzer
        self.writer = writer

    def run(
        self,
        source: str,
        table: str = "stg_raw_data",
        dedup_keys: list[str] | None = None,
        clean_rules: dict[str, Any] | None = None,
        analysis_config: dict[str, Any] | None = None,
        write_mode: str = "append",
        fetcher_kwargs: dict[str, Any] | None = None,
    ) -> CollectResult:
        """执行完整采集流水线

        Args:
            source: 数据源标识
            table: 目标数据库表名
            dedup_keys: 去重键字段列表
            clean_rules: 清洗规则（传给 DefaultCleaner.clean）
            analysis_config: 分析配置（传给 StatsAnalyzer.analyze）
            write_mode: 写入模式
            fetcher_kwargs: 传给 fetcher.fetch() 的额外参数

        Returns:
            CollectResult: 汇总所有阶段结果
        """
        task_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        result = CollectResult(task_id=task_id, source=source)

        logger.info(
            f"[Pipeline] 开始: task={task_id}, source={source}, table={table}"
        )

        # ── ① Fetch ──
        try:
            raw = self.fetcher.fetch(source, **(fetcher_kwargs or {}))
            result.raw = raw
        except Exception as e:
            return self._fail(result, started, "fetch", str(e))

        # ── ② Parse ──
        try:
            parsed = self.parser.parse(raw)
            result.parsed = parsed
            if not parsed.records:
                # 空数据集视为正常（今日无新数据），跳过后续步骤
                logger.info(f"[Pipeline] 数据源无新数据，采集完成")
                result.status = "success"
                result.elapsed_ms = (time.perf_counter() - started) * 1000
                return result
        except Exception as e:
            return self._fail(result, started, "parse", str(e))

        # ── ③ Clean ──
        try:
            rules = dict(clean_rules or {})
            if dedup_keys:
                rules.setdefault("dedup_keys", dedup_keys)
            cleaned = self.cleaner.clean(parsed.records, rules, source=source)
            result.cleaned = cleaned
        except Exception as e:
            return self._fail(result, started, "clean", str(e))

        # ── ④ Analyze (可选) ──
        if self.analyzer:
            try:
                analyzed = self.analyzer.analyze(
                    cleaned, config=analysis_config,
                )
                result.analyzed = analyzed
            except Exception as e:
                logger.warning(f"[Pipeline] 分析失败（非致命）: {e}")
                result.analyzed = AnalyzedData(source=source, records=cleaned.records)

        # ── ⑤ Write (可选) ──
        if self.writer:
            try:
                write = self.writer.write(cleaned.records, table, mode=write_mode)
                result.write = write
                if write.errors:
                    result.status = "partial"
            except Exception as e:
                return self._fail(result, started, "write", str(e))

        # ── 完成 ──
        if result.status != "partial":
            result.status = "success"
        result.elapsed_ms = (time.perf_counter() - started) * 1000

        logger.info(
            f"[Pipeline] 完成: task={task_id}, status={result.status}, "
            f"{result.elapsed_ms:.0f}ms"
        )
        return result

    def _fail(
        self,
        result: CollectResult,
        started: float,
        stage: str,
        error: str,
    ) -> CollectResult:
        result.status = "failed"
        result.error = f"[{stage}] {error}"
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        logger.error(f"[Pipeline] 失败 [{stage}]: {error}")
        return result
