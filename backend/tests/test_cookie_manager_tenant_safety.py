"""竞品 Cookie 只能回退到 URL 对应的平台。"""

from backend.competitor import cookie_manager


class _ConfigStore:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_config(self, key):
        return self.values.get(key, "")


def test_legacy_global_cookie_is_limited_to_its_recorded_platform(monkeypatch):
    store = _ConfigStore({
        "crawler_cookies": "legacy-session",
        "crawler_cookies_meta": '{"platform":"taobao"}',
    })
    monkeypatch.setattr(cookie_manager, "get_store", lambda: store)
    monkeypatch.setattr(cookie_manager, "crawler_cookies_raw", lambda: "")

    assert cookie_manager.get_cookies_for_url("https://item.taobao.com/item.htm") == (
        "legacy-session"
    )
    assert cookie_manager.get_cookies_for_url("https://item.jd.com/123") == ""
    assert cookie_manager.get_cookies_for_url("https://internal.example/admin") == ""
    assert cookie_manager.get_cookies_for_url(
        "https://attacker.example/?next=taobao.com"
    ) == ""
    assert cookie_manager.get_cookies_for_url(
        "https://taobao.com.attacker.example/"
    ) == ""
    assert cookie_manager.get_cookies_for_url(
        "https://sub.taobao.com/item"
    ) == "legacy-session"


def test_environment_cookie_is_limited_to_configured_platform(monkeypatch):
    monkeypatch.setattr(cookie_manager, "get_store", lambda: _ConfigStore())
    monkeypatch.setattr(cookie_manager, "crawler_cookies_raw", lambda: "env-session")
    monkeypatch.setattr(cookie_manager, "crawler_cookies_platform", lambda: "jd")

    assert cookie_manager.get_cookies_for_url("https://item.jd.com/123") == "env-session"
    assert cookie_manager.get_cookies_for_url("https://item.taobao.com/item.htm") == ""
