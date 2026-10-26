"""模型配置展示保留审计身份，测试原因不臆测。"""
from datetime import datetime, timezone

import pytest

from backend.services import model_config
from backend.services.provider_probe import ProbeResult, ProbeStep


@pytest.mark.asyncio
async def test_history_username_and_missing_user_keep_original_actor(monkeypatch):
    class Result:
        def mappings(self):
            return self

        def all(self):
            return [
                dict(id=i, object_type="role", object_key="main", old_value="a",
                     new_value="b", secret_fingerprint=None, operator=actor,
                     operator_username=name, changed_at=datetime.now(timezone.utc),
                     rollbackable=True)
                for i, (actor, name) in enumerate([
                    ("user:7", "张三"), ("user:999", None),
                    ("p1-verify-script", None), ("system", None),
                ])
            ]

    class Session:
        async def execute(self, statement, params):
            assert "LEFT JOIN auth.users" in str(statement)
            assert params == {"limit": 200}
            return Result()

    async def sessions():
        yield Session()

    monkeypatch.setattr(model_config, "get_session", sessions)
    rows = (await model_config.ModelConfigService().list_history())["items"]
    assert rows[0]["operatorName"] == "张三"
    assert rows[0]["operator"] == "user:7"
    assert rows[1]["operatorName"] == "未知用户（ID：999）"
    assert rows[2]["operatorName"] == "自动化：p1-verify-script"
    assert rows[3]["operatorName"] == "系统"


@pytest.mark.parametrize("reason,message", [
    ("api_key", "密钥无效"), ("billing", "额度不足"),
    ("rate_limited", "请求过于频繁"), ("model_name", "服务未接受"),
    ("unreachable", "无法连接"), ("timeout", "模型响应超时"),
    (None, "原因暂未确定"),
])
def test_probe_explains_actual_failure_without_exposing_raw(reason, message):
    result = ProbeResult(False, "L2", [
        ProbeStep("L2", "fail", "原有归因", detail="raw-provider-response", reason=reason),
    ]).to_dict()
    assert message in result["userMessage"]
    assert result["nextAction"]
    assert "raw-provider" not in result["userMessage"]
    assert result["steps"][0]["detail"] == "raw-provider-response"


def test_probe_success_does_not_claim_business_quality():
    result = ProbeResult(True, None).to_dict()
    assert result["userMessage"] == "模型调用成功"
    assert "不代表实际业务效果" in result["nextAction"]
