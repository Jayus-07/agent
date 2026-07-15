"""
report_generator.py — 报告生成主编排器

串联完整流程:
  ① 查询用户偏好 → 选模板
  ② DataFetcher 拉取数据 → JSON
  ③ 保存数据快照
  ④ TemplateEngine 渲染 → Markdown 初稿
  ⑤ ChartGenerator 生成图表 → base64 嵌入
  ⑥ LLMPolisher 润色 → 数字校验 → 最终报告
  ⑦ 记录用户偏好

作为 Agent 工具函数暴露: generate_report(report_type, filters) → str
"""

import os
import time
from datetime import datetime
from typing import Dict, Any, Optional

from backend.report.data_fetcher import (
    REPORT_REGISTRY, get_fetcher, SQLFetcher, APIFetcher,
)
from backend.report.template_engine import TemplateEngine
from backend.report.snapshot import save_snapshot, cleanup_old_snapshots
from backend.report.preference import preference_store
from backend.shared.logger import logger

# 图表和 LLM 润色模块惰性导入（可选依赖）
_chart_generator = None
_llm_polisher = None


def _get_chart_generator():
    global _chart_generator
    if _chart_generator is None:
        from backend.report.chart_generator import chart_generator as cg
        _chart_generator = cg
    return _chart_generator


def _get_llm_polisher():
    global _llm_polisher
    if _llm_polisher is None:
        from backend.report.llm_polisher import llm_polisher as lp
        _llm_polisher = lp
    return _llm_polisher


# =====================================================
# 报告生成器
# =====================================================

class ReportGenerator:
    """
    报告生成主编排器。

    用法:
        gen = ReportGenerator(output_dir="output")
        report = gen.generate("monthly_sales", {"month": "2026-05"}, user_id="user_001")
        print(report)  # 同时自动保存到 output/monthly_sales_2026-05-24T14-30-00.md
    """

    def __init__(
        self,
        db_config: dict = None,
        template_dir: str = None,
        snapshot_enabled: bool = True,
        output_dir: str = None,
    ):
        """
        参数:
            db_config:        PostgreSQL 连接配置（默认从 config.DB_CONFIG 读取）
            template_dir:     模板目录路径
            snapshot_enabled: 是否保存数据快照
            output_dir:       报告输出目录，不为空时自动将最终 Markdown 写入文件
                             文件命名: {report_type}_{timestamp}.md
        """
        from backend.config import DB_CONFIG
        self.db_config = db_config or dict(DB_CONFIG)
        self.snapshot_enabled = snapshot_enabled
        self.output_dir = output_dir
        self.template_engine = TemplateEngine(template_dir)

    # ---------------------------------------------------
    # 主入口
    # ---------------------------------------------------

    def generate(
        self,
        report_type: str,
        filters: dict = None,
        user_id: str = "default",
        polish: bool = True,
    ) -> str:
        """
        生成报告。

        参数:
            report_type: 报告类型（REPORT_REGISTRY 中的 key）
            filters:     筛选条件，如 {"dept_id": 1, "month": "2026-05"}
                        特殊字段:
                          _template: 指定模板文件名
                          _title:   指定报告标题
            user_id:     用户标识（用于偏好学习）
            polish:      是否启用 LLM 润色

        返回:
            Markdown 格式的报告字符串
        """
        filters = filters or {}
        start_time = time.time()

        # ── 获取报告配置 ──
        report_config = REPORT_REGISTRY.get(report_type)
        if not report_config:
            return (f"## 错误\n\n未知的报告类型: **{report_type}**\n\n"
                    f"可用类型: {', '.join(REPORT_REGISTRY.keys())}")

        logger.info(f"[ReportGen] ====== 开始生成报告: {report_type} ======")
        logger.info(f"[ReportGen] 筛选: {filters}, 用户: {user_id}, 润色: {polish}")

        try:
            # ① 查询用户偏好 → 选模板
            user_pref = preference_store.get(user_id, report_type)
            preferred_template = (
                filters.pop("_template", None) or
                user_pref.get("last_template")
            )
            report_title = filters.pop("_title", None)
            preferred_chart = user_pref.get("last_chart_type")

            # ② 拉取数据
            logger.info(f"[ReportGen] Step 1/5: 拉取数据...")
            fetcher = get_fetcher(report_config["source"], db_config=self.db_config)
            result = fetcher.fetch(report_config["source"], filters)

            if result["metadata"].get("row_count", 0) == 0:
                logger.warning(f"[ReportGen] 查询结果为空")

            # ③ 保存数据快照
            if self.snapshot_enabled:
                logger.info(f"[ReportGen] Step 2/5: 保存快照...")
                save_snapshot(report_type, result, filters)

            # ④ 模板渲染
            logger.info(f"[ReportGen] Step 3/5: 模板渲染 "
                        f"(模板={preferred_template or 'auto'})...")
            draft = self.template_engine.render(
                report_type, result,
                template_name=preferred_template,
                title=report_title,
            )

            # ⑤ 图表生成
            chart_md = ""
            chart_configs = report_config.get("charts", [])
            if chart_configs and result["data"]:
                logger.info(f"[ReportGen] Step 4/5: 生成图表 ({len(chart_configs)} 个)...")
                chart_md = _get_chart_generator().generate(chart_configs, result["data"])

            # 将图表插入 draft（在第一个 ## 标题后）
            if chart_md:
                draft = self._insert_charts(draft, chart_md)

            # ⑥ LLM 润色
            if polish:
                logger.info(f"[ReportGen] Step 5/5: LLM 润色...")
                final = _get_llm_polisher().polish(draft)
            else:
                final = draft

            # ⑦ 输出到文件
            output_path = ""
            if self.output_dir:
                output_path = self._write_output(report_type, final)

            # ⑧ 记录偏好
            chart_type = chart_configs[0]["type"] if chart_configs else None
            if preferred_chart and chart_configs:
                chart_type = preferred_chart
            preference_store.record(
                user_id, report_type,
                template_name=preferred_template or report_config.get("templates", [None])[0],
                chart_type=chart_type,
            )

            # 定期清理过期快照（失败仅记录，不影响主报告生成）
            try:
                cleanup_old_snapshots()
            except Exception as e:
                logger.warning(f"[ReportGen] 快照清理失败（非致命）: {e}")

            elapsed = time.time() - start_time
            logger.info(f"[ReportGen] ====== 报告完成: {len(final)} 字符, "
                        f"耗时 {elapsed:.2f}s ======")

            return final

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"[ReportGen] 报告生成失败 ({elapsed:.2f}s): {e}")
            raise

    # ---------------------------------------------------
    # 文件输出
    # ---------------------------------------------------

    def _write_output(self, report_type: str, content: str) -> str:
        """将最终报告写入 Markdown 文件"""
        from datetime import datetime
        os.makedirs(self.output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        fname = f"{report_type}_{ts}.md"
        fpath = os.path.join(self.output_dir, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"[ReportGen] 报告已保存: {fpath}")
        return fpath

    # ---------------------------------------------------
    # 图表插入
    # ---------------------------------------------------

    def _insert_charts(self, draft: str, chart_md: str) -> str:
        """
        将图表 Markdown 插入到 draft 中。
        默认插入到第一个 ## 标题之前，如果没有则追加到末尾。
        """
        lines = draft.split("\n")

        # 找第一个 ## 标题的位置
        insert_at = None
        for i, line in enumerate(lines):
            if line.strip().startswith("## "):
                insert_at = i
                break

        if insert_at is not None:
            lines.insert(insert_at, "\n" + chart_md)
        else:
            lines.append("\n" + chart_md)

        return "\n".join(lines)


# =====================================================
# Agent 工具函数
# =====================================================

_generator: Optional[ReportGenerator] = None


def _get_generator(output_dir: str = None) -> ReportGenerator:
    """懒加载全局 ReportGenerator（首次调用时实例化）"""
    global _generator
    if _generator is None:
        _generator = ReportGenerator(output_dir=output_dir)
        logger.info("[ReportGen] ReportAgent 初始化完成")
    elif output_dir is not None:
        _generator.output_dir = output_dir
    return _generator


def init_report_agent(
    db_config: dict = None,
    template_dir: str = None,
    output_dir: str = None,
) -> ReportGenerator:
    """构造并缓存 ReportGenerator。可重复调用重新初始化（demo/测试用）。"""
    global _generator
    _generator = ReportGenerator(
        db_config=db_config, template_dir=template_dir, output_dir=output_dir,
    )
    logger.info("[ReportGen] ReportAgent 初始化完成")
    return _generator


def generate_report(
    report_type: str,
    filters: dict = None,
    user_id: str = "default",
    polish: bool = True,
    output_dir: str = None,
) -> str:
    """报告生成入口（Agent 工具函数）。"""
    return _get_generator(output_dir=output_dir).generate(report_type, filters, user_id, polish)
