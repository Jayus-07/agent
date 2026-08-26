# -*- coding: utf-8 -*-
"""模板引擎必需列校验回归测试。

fix f22：旧实现把中英别名存成扁平 set 再按 list(set) 顺序两两切组，
set 迭代顺序受字符串哈希随机化影响，英中别名可能被随机拆散
（如 customer_count 与 avg_order_value 同组），日报列明明齐全却
间歇性误判缺列降级（MiniMax 切换复测暴露）。
"""
from backend.business_report.template_engine import TemplateEngine


def _engine():
    return TemplateEngine()


class TestRequiredColumnGroups:
    def test_chinese_alias_data_passes(self):
        """data_fetcher 的中文别名列齐全 → 校验必须通过。"""
        data = [{"日期": "2026-08-20", "渠道": "线上", "订单数": 1,
                 "销售额": 199.0, "下单客户数": 1, "客单价": 199.0}]
        ok, missing = _engine()._check_required_columns("daily_sales.j2", data)
        assert ok, f"中文别名列齐全却被判缺列: {missing}"

    def test_english_alias_data_passes(self):
        """英文别名列齐全 → 校验必须通过。"""
        data = [{"date": "2026-08-20", "channel": "online", "order_count": 1,
                 "sales_amount": 199.0, "customer_count": 1, "avg_order_value": 199.0}]
        ok, missing = _engine()._check_required_columns("daily_sales.j2", data)
        assert ok, f"英文别名列齐全却被判缺列: {missing}"

    def test_truly_missing_group_detected(self):
        """真缺客单价组（客单价/avg_order_value 都没有）→ 必须报缺。"""
        data = [{"日期": "2026-08-20", "渠道": "线上", "订单数": 1,
                 "销售额": 199.0, "下单客户数": 1}]
        ok, missing = _engine()._check_required_columns("daily_sales.j2", data)
        assert not ok
        assert any("客单价" in m for m in missing)

    def test_grouping_stable_across_hash_seeds(self):
        """校验结果不受 set 哈希随机化影响（子进程不同 PYTHONHASHSEED 复验）。"""
        import os
        import subprocess
        import sys
        code = (
            "from backend.business_report.template_engine import TemplateEngine\n"
            "e = TemplateEngine()\n"
            "data = [{'日期': 'd', '渠道': 'c', '订单数': 1, '销售额': 1.0,"
            " '下单客户数': 1, '客单价': 1.0}]\n"
            "ok, missing = e._check_required_columns('daily_sales.j2', data)\n"
            "print('OK' if ok else f'MISS {missing}')\n"
        )
        for seed in ("0", "1", "42"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            out = subprocess.run(
                [sys.executable, "-c", code], capture_output=True,
                env=env, cwd=".", timeout=60,
            )
            stdout = out.stdout.decode("utf-8", errors="replace").strip()
            stderr = out.stderr.decode("utf-8", errors="replace")
            last_line = stdout.splitlines()[-1] if stdout else ""
            assert last_line == "OK", (
                f"seed={seed} 结果不稳定: {stdout} {stderr[-200:]}"
            )
