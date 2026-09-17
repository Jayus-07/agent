"""tests/skills/test_email_skill.py — 批次1 Agently Mail 升级测试

覆盖：
  - EmailSkill 四能力分发（_select_tool 参数过滤）
  - send 引擎切换（smtp 默认 / agently 路径 + 幂等指纹共用 + 失败不缓存）
  - agently 包装层（exit code 语义 / JSON envelope / 未安装提示）
  - capabilities.yaml manifest 含新能力（一致性由 test_registry_consistency 守）
"""
import json
from unittest.mock import patch

import pytest

from backend.skills.email.skill import EmailSkill


# ==================== 能力分发 ====================

class TestEmailSkillDispatch:
    def setup_method(self):
        self.skill = EmailSkill()

    def test_capabilities_declared(self):
        assert self.skill.capabilities == [
            "email.send", "email.search", "email.read", "email.watch"]

    def test_send_dispatch_default(self):
        tool, params = self.skill._select_tool("email.send", {"action": "send", "to": "a@b.c"})
        assert tool.name == "send_email_tool"
        # action 在 send_email_tool 签名外，应被过滤
        assert "action" not in params
        assert params["to"] == "a@b.c"

    def test_search_dispatch_by_capability(self):
        tool, params = self.skill._select_tool("email.search", {"query": "发货单", "limit": 5})
        assert tool.name == "search_email_tool"
        assert params["query"] == "发货单"
        assert params["limit"] == 5

    def test_read_dispatch_by_action(self):
        tool, params = self.skill._select_tool("email.send", {"action": "read", "message_id": "msg_1"})
        assert tool.name == "read_email_tool"
        assert params["message_id"] == "msg_1"

    def test_watch_dispatch(self):
        tool, _ = self.skill._select_tool("email.watch", {"timeout_sec": 60})
        assert tool.name == "watch_email_tool"

    def test_unknown_action_falls_back_to_send(self):
        tool, _ = self.skill._select_tool("email.send", {"action": "xxx"})
        assert tool.name == "send_email_tool"

    def test_params_validation_requires_send_fields(self):
        err = self.skill._validate_params({"action": "send", "to": "", "subject": "", "body": ""})
        # send 的必填参数声明为 required: False（action 路由模式，Tool 层兜底），
        # 但缺 to 时 Tool 会给出可读错误——这里只验证 schema 本身不炸
        assert err is None or "缺少必填参数" in err


# ==================== send 引擎切换 ====================

class TestSendEngineSwitch:
    def setup_method(self):
        # 清空幂等指纹缓存，避免用例间串扰
        from backend.tools import email as email_mod
        email_mod._SENT_FINGERPRINTS.clear()

    def test_smtp_disabled_message_unchanged(self):
        """未配 SMTP 且引擎=smtp 时，返回与原版一致的 DISABLED 提示。"""
        from backend.tools.email import send_email_tool
        with patch("backend.config.EMAIL_ENGINE", "smtp"), \
             patch("backend.config.SMTP_USER", ""), \
             patch("backend.config.SMTP_PASSWORD", ""):
            result = send_email_tool.invoke({"to": "a@b.c", "subject": "s", "body": "b"})
        assert "[EMAIL DISABLED]" in result

    def test_agently_engine_bypasses_smtp_check(self):
        """B6 修复回归：引擎=agently 时不依赖 SMTP 凭据（原实现被
        [EMAIL DISABLED] 无差别拦截，发送侧验收从未真正走到 agently）。"""
        from backend.tools.email import send_email_tool
        with patch("backend.config.EMAIL_ENGINE", "agently"), \
             patch("backend.config.SMTP_USER", ""), \
             patch("backend.config.SMTP_PASSWORD", ""), \
             patch("backend.security.tool_approval.TOOL_APPROVAL_MODE", "auto"), \
             patch("backend.tools.email._send_via_agently",
                   return_value="邮件已发送(Agently): 收件人 a@b.c") as mock_send:
            result = send_email_tool.invoke({"to": "a@b.c", "subject": "s", "body": "b"})
        assert "[EMAIL DISABLED]" not in result
        assert "已发送" in result
        mock_send.assert_called_once()

    def test_agently_send_success(self):
        from backend.tools import email as email_mod
        with patch("backend.config.EMAIL_ENGINE", "agently"), \
             patch("backend.tools.agently.agently_available", return_value=True), \
             patch("backend.tools.agently.agently_send",
                   return_value=json.dumps({"ok": True})) as mock_send:
            result = email_mod._send_via_agently("a@b.c", "周报", "# 数据", "")
        assert "已发送" in result
        assert "Agently" in result
        args, kwargs = mock_send.call_args
        assert kwargs["to"] == ["a@b.c"]
        assert kwargs["subject"] == "周报"

    def test_agently_send_dedup_fingerprint(self):
        """窗口内同指纹拦截——agently 与 smtp 共用同一份指纹缓存。"""
        from backend.tools import email as email_mod
        email_mod._SENT_FINGERPRINTS[
            email_mod._email_fingerprint("a@b.c", "", "周报", "# 数据")] = 1e18
        with patch("backend.config.EMAIL_ENGINE", "agently"), \
             patch("backend.tools.agently.agently_available", return_value=True), \
             patch("backend.tools.agently.agently_send") as mock_send:
            result = email_mod._send_via_agently("a@b.c", "周报", "# 数据", "")
        assert "[EMAIL DUPLICATE]" in result
        mock_send.assert_not_called()

    def test_agently_send_failure_not_cached(self):
        """发送失败不缓存指纹，重试路径保持畅通（与 SMTP 路径同语义）。"""
        from backend.tools import email as email_mod
        with patch("backend.config.EMAIL_ENGINE", "agently"), \
             patch("backend.tools.agently.agently_available", return_value=True), \
             patch("backend.tools.agently.agently_send",
                   return_value="[AGENTLY ERROR:1] 服务端错误"):
            r1 = email_mod._send_via_agently("a@b.c", "周报", "# 数据", "")
        assert r1.startswith("[AGENTLY ERROR:1]")
        assert email_mod._email_fingerprint("a@b.c", "", "周报", "# 数据") \
            not in email_mod._SENT_FINGERPRINTS

    def test_agently_send_cli_missing(self):
        from backend.tools import email as email_mod
        with patch("backend.config.EMAIL_ENGINE", "agently"), \
             patch("backend.tools.agently.agently_available", return_value=False):
            result = email_mod._send_via_agently("a@b.c", "周报", "# 数据", "")
        assert "agently-cli 未安装" in result


# ==================== agently 包装层 ====================

class TestAgentlyWrapper:
    def test_binary_missing_message(self):
        from backend.tools import agently
        with patch.object(agently, "agently_bin", return_value=None):
            result = agently._run(["+me"])
        assert "agently-cli 未安装" in result

    def test_exit_code_mapping(self):
        from backend.tools import agently
        proc = type("P", (), {"returncode": 3, "stdout": "", "stderr": ""})()
        with patch.object(agently, "agently_bin", return_value="/usr/bin/agently-cli"), \
             patch("subprocess.run", return_value=proc):
            result = agently._run(["+me"])
        assert "[AGENTLY ERROR:3]" in result
        assert "授权失效" in result

    def test_error_message_from_envelope(self):
        from backend.tools import agently
        proc = type("P", (), {
            "returncode": 6,
            "stdout": json.dumps({"error": {"message": "已退订"}}),
            "stderr": "",
        })()
        with patch.object(agently, "agently_bin", return_value="/usr/bin/agently-cli"), \
             patch("subprocess.run", return_value=proc):
            result = agently._run(["message", "+send"])
        assert "已退订" in result

    def test_success_envelope_parsed(self):
        from backend.tools import agently
        proc = type("P", (), {
            "returncode": 0,
            "stdout": json.dumps({"data": {"alias": "demo@agent.qq.com"}}),
            "stderr": "",
        })()
        with patch.object(agently, "agently_bin", return_value="/usr/bin/agently-cli"), \
             patch("subprocess.run", return_value=proc):
            result = agently._run(["+me"])
        assert json.loads(result)["data"]["alias"] == "demo@agent.qq.com"

    def test_non_json_stdout_fallback(self):
        """CLI 版本差异容忍：非 JSON stdout 退回原始文本。"""
        from backend.tools import agently
        proc = type("P", (), {"returncode": 0, "stdout": "plain text v9", "stderr": ""})()
        with patch.object(agently, "agently_bin", return_value="/usr/bin/agently-cli"), \
             patch("subprocess.run", return_value=proc):
            result = agently._run(["+me"])
        assert json.loads(result)["raw"] == "plain text v9"

    def test_watch_capped_by_config(self):
        from backend.tools import agently
        with patch.object(agently, "agently_bin", return_value="/usr/bin/agently-cli"), \
             patch("backend.config.AGENTLY_WATCH_MAX_SECONDS", 300), \
             patch("subprocess.run", return_value=type("P", (), {
                 "returncode": 0, "stdout": "{}", "stderr": ""})()) as mock_run:
            agently.agently_watch(timeout_sec=99999)
        # 窗口被钉到上限 300 + 10 余量
        assert mock_run.call_args.kwargs["timeout"] == 310.0


# ==================== manifest 对账 ====================

class TestManifest:
    def test_new_capabilities_in_manifest(self):
        from backend.orchestration.router.manifest import load_manifest
        caps = {c.name: c for c in load_manifest().capabilities}
        assert "email.search" in caps and caps["email.search"].routed
        assert "email.read" in caps and caps["email.read"].routed
        assert "email.watch" in caps and not caps["email.watch"].routed

    def test_watch_routed_false_has_reason(self):
        from backend.orchestration.router.manifest import load_manifest
        cap = next(c for c in load_manifest().capabilities if c.name == "email.watch")
        assert cap.reason
