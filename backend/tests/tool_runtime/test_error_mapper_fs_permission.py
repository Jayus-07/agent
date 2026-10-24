"""error_mapper 文件系统权限误标回归（2026-10-25）。

实测：报告快照写只读镜像层抛 PermissionError（Errno 13），按消息文本
"permission" 匹配被误标 unauthorized（账号无权）——基础设施故障被包装成
鉴权语义，误导用户与排障方向。类型判定优先，字符串兜底。
"""
from backend.core.tool_runtime.error_mapper import map_exception
from backend.core.tool_runtime.models import ToolStatus


def test_native_permission_error_is_infra_not_authz():
    exc = PermissionError(13, "Permission denied", "/app/backend/data")
    cls = map_exception(exc)
    assert cls.status is ToolStatus.UNAVAILABLE
    assert cls.error_code == "infra_error"


def test_wrapped_permission_message_is_infra_not_authz():
    """异常被包装丢失类型时按消息特征兜底（Errno 13 + denied）。"""
    exc = RuntimeError(
        "报告生成失败 (0.13s): [Errno 13] Permission denied: '/app/backend/data'")
    cls = map_exception(exc)
    assert cls.status is ToolStatus.UNAVAILABLE
    assert cls.error_code == "infra_error"


def test_authz_unauthorized_still_unauthorized():
    """真鉴权语义（401/403 文案）不受影响。"""
    cls = map_exception(RuntimeError("HTTP 403: forbidden for this user"))
    assert cls.status is ToolStatus.UNAUTHORIZED
    cls2 = map_exception(RuntimeError("unauthorized: invalid api key"))
    assert cls2.status is ToolStatus.UNAUTHORIZED
