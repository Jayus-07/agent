"""backend/tests/test_anti_ban.py — 防封策略中枢测试

覆盖 anti_ban.py 的五大防线:
  1. 限流: 平台日配额 / 全局预算 / 随机间隔
  2. 指数退避: 失败连击 → 间隔倍增（上限 8 倍）
  3. 一票停熔断: L1 平台当日停采 / L2 全局停采 48h / 人工恢复
  4. Cookie 疑似失效标记与清除
  5. robots.txt 合规检查 / 行为拟人参数 / 观测 stats

隔离策略: 全部使用临时 CompetitorStore(db_path=tmp) 并显式传 store= 参数。
"""
import json
import os
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from backend.competitor import anti_ban
from backend.competitor.anti_ban import (
    BACKOFF_CAP,
    BudgetExhaustedError,
    GlobalHaltError,
    PlatformStoppedError,
)
from backend.competitor.store import CompetitorStore


@pytest.fixture
def store(tmp_path):
    return CompetitorStore(db_path=str(tmp_path / "anti_ban.db"))


# ── ① 限流 ────────────────────────────────────────────


class TestRateLimiter:
    def test_first_acquire_no_wait(self, store):
        """首次请求无需等待，计数 +1"""
        wait_s = anti_ban.acquire("jd", wait=False, store=store)
        assert wait_s == 0.0
        state = anti_ban._load_state(store)
        assert state["platforms"]["jd"]["count_today"] == 1
        assert state["global_used"] == 1

    def test_second_acquire_needs_wait(self, store):
        """紧接着的第二次请求必须等待（间隔下限以内不可能放行）"""
        anti_ban.acquire("jd", wait=False, store=store)
        wait_s = anti_ban.acquire("jd", wait=False, store=store)
        prof = anti_ban.PROFILES["jd"]
        assert prof.min_interval - 1 <= wait_s <= prof.max_interval

    def test_daily_cap_enforced(self, store):
        """平台日配额耗尽 → BudgetExhaustedError"""
        cap = anti_ban.PROFILES["jd"].daily_cap
        for _ in range(cap):
            anti_ban.acquire("jd", wait=False, store=store)
        with pytest.raises(BudgetExhaustedError):
            anti_ban.acquire("jd", wait=False, store=store)

    def test_global_budget_enforced(self, store):
        """全局每日预算耗尽 → BudgetExhaustedError（先于平台配额触发）"""
        with patch.object(anti_ban, "GLOBAL_DAILY_BUDGET", 3):
            for _ in range(3):
                anti_ban.acquire("generic", wait=False, store=store)
            with pytest.raises(BudgetExhaustedError):
                anti_ban.acquire("generic", wait=False, store=store)

    def test_tmall_shares_taobao_quota(self, store):
        """tmall 归一到 taobao，共享配额与状态"""
        anti_ban.acquire("tmall", wait=False, store=store)
        state = anti_ban._load_state(store)
        assert "taobao" in state["platforms"]
        assert state["platforms"]["taobao"]["count_today"] == 1


# ── ② 指数退避 ────────────────────────────────────────


class TestExponentialBackoff:
    def test_interval_grows_with_fail_streak(self, store):
        """失败连击 n → 间隔 × 2^n"""
        anti_ban.acquire("jd", wait=False, store=store)
        for _ in range(3):
            anti_ban.report_failure("jd", "timeout", store=store)
        state = anti_ban._load_state(store)
        wait_s = anti_ban.acquire("jd", wait=False, store=store)
        prof = anti_ban.PROFILES["jd"]
        assert prof.min_interval * 8 - 1 <= wait_s <= prof.max_interval * 8

    def test_backoff_capped(self, store):
        """退避倍数上限 BACKOFF_CAP（避免无限膨胀）"""
        for _ in range(10):
            anti_ban.report_failure("jd", "err", store=store)
        state = anti_ban._load_state(store)
        assert state["platforms"]["jd"]["fail_streak"] == 10
        base = anti_ban.interval_for("jd", state)
        prof = anti_ban.PROFILES["jd"]
        assert base <= prof.max_interval * BACKOFF_CAP

    def test_success_resets_streak(self, store):
        """成功后失败连击清零，间隔回归基础范围"""
        anti_ban.report_failure("jd", "err", store=store)
        anti_ban.report_failure("jd", "err", store=store)
        anti_ban.report_success("jd", store=store)
        state = anti_ban._load_state(store)
        assert state["platforms"]["jd"]["fail_streak"] == 0


# ── ③ 一票停熔断 ──────────────────────────────────────


class TestCircuitBreaker:
    def test_l1_one_strike_stop(self, store):
        """L1: 命中风控 → 该平台当日停采 + 事件记录"""
        anti_ban.report_blocked("jd", "https://item.jd.com/1.html", store=store)
        with pytest.raises(PlatformStoppedError):
            anti_ban.acquire("jd", wait=False, store=store)
        events = store.recent_events()
        assert any(e["event_type"] == "blocked" for e in events)
        # 其他平台不受影响
        anti_ban.acquire("generic", wait=False, store=store)

    def test_l1_stops_until_end_of_day(self, store):
        """L1 停采持续到当日结束"""
        anti_ban.report_blocked("jd", "u", store=store)
        state = anti_ban._load_state(store)
        stopped_until = state["platforms"]["jd"]["stopped_until"]
        end_of_day = datetime.now().replace(hour=23, minute=59, second=59).timestamp()
        assert time.time() < stopped_until <= end_of_day + 1

    def test_l2_global_halt_after_second_block(self, store):
        """L2: 24h 内第 2 次 blocked → 全局停采 48h，所有平台拒绝"""
        anti_ban.report_blocked("jd", "u1", store=store)
        anti_ban.report_blocked("taobao", "u2", store=store)
        with pytest.raises(GlobalHaltError):
            anti_ban.acquire("generic", wait=False, store=store)
        with pytest.raises(GlobalHaltError):
            anti_ban.acquire("jd", wait=False, store=store)
        s = anti_ban.stats(store)
        assert s["halted"] is True
        assert s["halt_remaining_h"] > 47
        assert any(e["event_type"] == "halt" for e in s["recent_events"])

    def test_resume_after_halt(self, store):
        """人工确认后 L2 解除"""
        anti_ban.report_blocked("jd", "u1", store=store)
        anti_ban.report_blocked("jd", "u2", store=store)
        with pytest.raises(GlobalHaltError):
            anti_ban.acquire("generic", wait=False, store=store)
        anti_ban.resume_after_halt(store=store)
        # generic 无停采标记，应放行（jd 仍在 L1 停采中）
        anti_ban.acquire("generic", wait=False, store=store)


# ── ④ Cookie 疑似失效 ─────────────────────────────────


class TestCookieSuspect:
    def test_login_redirect_marks_suspect(self, store):
        anti_ban.report_login_redirect("taobao", "u", store=store)
        assert anti_ban.is_cookie_suspect("taobao", store=store) is True
        assert anti_ban.is_cookie_suspect("jd", store=store) is False
        events = store.recent_events()
        assert any(e["event_type"] == "login_redirect" for e in events)

    def test_clear_suspect_resets_clean_slate(self, store):
        """扫码登录成功 → 清除标记 + 重置失败连击与停采（干净起点）"""
        anti_ban.report_login_redirect("taobao", "u", store=store)
        anti_ban.report_failure("taobao", "err", store=store)
        anti_ban.clear_cookie_suspect("taobao", store=store)
        assert anti_ban.is_cookie_suspect("taobao", store=store) is False
        state = anti_ban._load_state(store)
        assert state["platforms"]["taobao"]["fail_streak"] == 0

    def test_tmall_suspect_normalized(self, store):
        anti_ban.report_login_redirect("tmall", "u", store=store)
        assert anti_ban.is_cookie_suspect("taobao", store=store) is True


# ── ⑤ robots.txt 合规 ─────────────────────────────────


class TestRobotsCompliance:
    def _seed_cache(self, store, domain, text):
        cache = {domain: {"fetched_at": time.time(), "text": text}}
        store.set_config("anti_ban:robots", json.dumps(cache))

    def test_disallow_skipped(self, store):
        self._seed_cache(store, "example.com", "User-agent: *\nDisallow: /private\n")
        assert anti_ban.robots_allowed("https://example.com/private/x", store=store) is False
        assert anti_ban.robots_allowed("https://example.com/public", store=store) is True
        # 跳过行为应记录事件
        events = store.recent_events()
        assert any(e["event_type"] == "robots_skip" for e in events)

    def test_no_robots_allows(self, store):
        """无 robots.txt（或拉取失败）→ 放行"""
        self._seed_cache(store, "example.com", "")
        assert anti_ban.robots_allowed("https://example.com/any", store=store) is True

    def test_warn_only_override(self, store, monkeypatch):
        """ROBOTS_OVERRIDE=warn_only → 仅告警不拦截（用户显式决策）"""
        monkeypatch.setenv("ROBOTS_OVERRIDE", "warn_only")
        assert anti_ban.robots_allowed("https://anything.com/x", store=store) is True

    def test_expired_cache_refetch(self, store):
        """缓存过期 → 重新拉取（mock 网络）"""
        old = {"example.com": {"fetched_at": time.time() - 999999, "text": "Disallow: /"}}
        store.set_config("anti_ban:robots", json.dumps(old))
        with patch.object(anti_ban, "_fetch_robots", return_value="User-agent: *\nAllow: /\n"):
            assert anti_ban.robots_allowed("https://example.com/x", store=store) is True


# ── 行为拟人 ──────────────────────────────────────────


class TestHumanize:
    def test_delay_in_human_range(self):
        for _ in range(20):
            h = anti_ban.humanize()
            assert 2.5 <= h["delay_before_return_html"] <= 6.0

    def test_scroll_script_present(self):
        h = anti_ban.humanize()
        assert "scrollTo" in h["_humanize_js"]
        assert "Math.random" in h["_humanize_js"]


# ── Cookie 龄期 ───────────────────────────────────────


class TestCookieAge:
    def test_no_cookie_returns_none(self, store):
        with patch("backend.competitor.cookie_manager.get_store", return_value=store):
            assert anti_ban.cookie_age_days("jd", store=store) is None

    def test_age_and_stale_detection(self, store):
        with patch("backend.competitor.cookie_manager.get_store", return_value=store):
            from backend.competitor import cookie_manager
            cookie_manager.save_cookies("jd", "thor=x; pt_key=y", "qr")
            # 伪造 25 天前的保存时间
            old = (datetime.now() - timedelta(days=25)).isoformat(timespec="seconds")
            store.set_config(
                "crawler_cookies_meta:jd",
                json.dumps({"source": "qr", "saved_at": old}),
            )
            age = anti_ban.cookie_age_days("jd", store=store)
            assert age is not None and 24.9 <= age <= 25.1
            assert "jd" in anti_ban.stale_cookie_platforms(store=store)


# ── 观测 ─────────────────────────────────────────────


class TestStats:
    def test_stats_structure(self, store):
        anti_ban.acquire("jd", wait=False, store=store)
        anti_ban.report_blocked("jd", "u", store=store)
        s = anti_ban.stats(store)
        assert s["global_budget"] == anti_ban.GLOBAL_DAILY_BUDGET
        assert s["global_used"] == 1
        jd = s["platforms"]["jd"]
        assert jd["count_today"] == 1
        assert jd["stopped"] is True
        assert jd["blocked_24h"] == 1
        assert s["halted"] is False
        assert isinstance(s["recent_events"], list)
