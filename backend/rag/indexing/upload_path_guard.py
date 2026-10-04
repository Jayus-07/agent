"""上传入口的测试产物路径识别。"""

from __future__ import annotations

import re


_TEST_COMPONENT = re.compile(
    r"^(?:pytest(?:[-_].*)?|tmp|temp|agent_test_data(?:[-_].*)?)$",
    re.IGNORECASE,
)


def test_artifact_path_reason(path: str | None) -> str | None:
    """识别测试临时路径；正常业务目录和文件名返回 None。"""
    if not path:
        return None
    normalized = str(path).replace("\\", "/")
    components = [part for part in normalized.split("/") if part]
    for component in components:
        if _TEST_COMPONENT.fullmatch(component):
            return "test_artifact_path"
    return None
