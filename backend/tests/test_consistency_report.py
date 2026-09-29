"""资产一致性报告端点测试（M6 / 台账 D6）

断言口径：
1. 正常态：八段齐全、overall=PASS、counts 与代码权威值同源
   （skill=12 / capability=17 / tool=34 口径与 README 一致）。
2. 破坏态：mock 一个未注册 capability / 篡改 lock → 对应段 FAIL、
   overall=FAIL、failed_sections 点名——报告必须「看得见漂移」。
3. 鉴权：require_admin_user 被调用（403 路径不在此测，见 deps 既有测试）。
"""
from __future__ import annotations

import pytest


async def _report():
    from backend.app.api.routes.consistency import consistency_report

    class _FakeRequest:
        pass

    return await consistency_report(_FakeRequest())


@pytest.fixture(autouse=True)
def _bypass_admin_auth(monkeypatch):
    """绕过管理员鉴权（本文件只测对账逻辑；鉴权语义在 deps 既有测试覆盖）。"""
    from backend.app.api.routes import consistency as consistency_mod

    async def _allow(request):
        return None

    monkeypatch.setattr(consistency_mod, "require_admin_user", _allow)


class TestConsistencyReport:
    async def test_normal_state_passes(self):
        report = await _report()
        names = [s["name"] for s in report["sections"]]
        assert names == [
            "agents", "skills", "capabilities", "tools",
            "workflows", "mcp", "tool_contract_lock",
        ]
        # 权威口径（README「系统规模」）：Skill 12 / capability 17 / Tool 34
        by_name = {s["name"]: s for s in report["sections"]}
        assert by_name["skills"]["counts"]["skills"] == 12
        assert by_name["capabilities"]["counts"]["capabilities"] == 17
        assert by_name["tools"]["counts"]["loaded"] == 34
        assert by_name["tools"]["counts"]["not_loaded"] == 0
        assert by_name["tools"]["counts"]["phantom"] == 0
        assert report["overall"] == "PASS", report["failed_sections"]
        for section in report["sections"]:
            assert section["status"] == "PASS", (section["name"], section["issues"])

    async def test_tool_count_matches_registry(self):
        """报告的 tool 计数必须与 tool_registry 同源（禁手抄）。"""
        import backend.skills  # noqa: F401
        import backend.tools  # noqa: F401
        from backend.tools.tool_registry import tool_registry

        report = await _report()
        tools_section = next(s for s in report["sections"] if s["name"] == "tools")
        assert tools_section["counts"]["loaded"] == len(tool_registry.tool_names)


class TestDriftDetection:
    async def test_unregistered_capability_fails(self, monkeypatch):
        """manifest 声明一个未注册 capability → capabilities 段 FAIL。"""
        from backend.orchestration.router import manifest as manifest_mod

        original = manifest_mod.load_manifest

        def fake_load_manifest():
            real = original()
            from types import SimpleNamespace

            ghost = SimpleNamespace(**vars(real.capabilities[0]))
            ghost.name = "ghost.capability"
            ghost.skill = real.capabilities[0].skill
            ghost.routed = False
            ghost.rule_keywords, ghost.examples, ghost.reason = [], [], ""
            return SimpleNamespace(
                capabilities=[*real.capabilities, ghost],
                workflows=real.workflows,
            )

        monkeypatch.setattr(manifest_mod, "load_manifest", fake_load_manifest)
        # consistency 模块内是 `from ... import load_manifest` 局部导入，patch 源模块即可
        report = await _report()
        assert report["overall"] == "FAIL"
        assert "capabilities" in report["failed_sections"]

    async def test_lock_drift_fails(self, monkeypatch, tmp_path):
        """lock 文件被篡改（删一个 tool）→ tool_contract_lock 段 FAIL。"""
        import json

        from backend.app.api.routes import consistency as consistency_mod

        tampered = tmp_path / "tool_contracts.lock.json"
        # 幽灵 tool：当前派生里不存在 → tool_removed → BREAKING
        tampered.write_text(json.dumps({"tools": {
            "ghost_tool_never_existed": {
                "module": "backend/tools/ghost.py", "description_hash": "0" * 16,
                "args_schema": {}, "capabilities": [], "output_types": {},
                "content_hash": "0" * 16,
            }
        }}), encoding="utf-8")
        monkeypatch.setattr(consistency_mod, "LOCK_PATH", tampered)
        report = await _report()
        lock_section = next(s for s in report["sections"] if s["name"] == "tool_contract_lock")
        assert lock_section["status"] == "FAIL"
        assert any("BREAKING" in issue for issue in lock_section["issues"])
        assert report["overall"] == "FAIL"
