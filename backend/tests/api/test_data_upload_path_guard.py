"""data.py /data/upload 路径守卫与大小上限回归（审计 P0-1）。

两条修复线汇合后的语义锁定（2026-11-03，audit-remediation-r1）：
- main 0f74940（S3/S4）：admin 鉴权 + 磁盘名服务端净化（re.sub 限安全字符）
  + realpath 包含校验 —— 恶意文件名不逃逸（净化后落盘在 UPLOAD_DIR 内）；
- 本分支补充：流式读取 + DATA_UPLOAD_MAX_BYTES 上限（S4 未覆盖的内存缺口）。

直接调用路由函数（不起 TestClient）；admin 依赖以 OperatorIdentity 桩注入。
"""
import os

import pytest

from backend.app.api.deps import OperatorIdentity
from backend.app.api.routes import data as data_routes


class FakeUploadFile:
    """最小 UploadFile 替身：async read(n) 按块返回（与 rag_upload 测试同款）。"""

    def __init__(self, filename, data: bytes):
        self.filename = filename
        self._data = data
        self._pos = 0

    async def read(self, n: int = -1) -> bytes:
        if n <= 0:
            chunk = self._data[self._pos:]
            self._pos = len(self._data)
        else:
            chunk = self._data[self._pos:self._pos + n]
            self._pos += len(chunk)
        return chunk


def _operator() -> OperatorIdentity:
    return OperatorIdentity(role="admin", actor="user:u-1")


@pytest.fixture(autouse=True)
def _isolate_upload_dir(monkeypatch, tmp_path):
    """把 UPLOAD_DIR 指到临时目录，避免污染仓库 backend/data。"""
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setattr(data_routes, "UPLOAD_DIR", str(upload_root))
    yield str(upload_root)


@pytest.mark.asyncio
async def test_dotdot_filename_never_escapes_upload_dir(_isolate_upload_dir):
    """../../tmp/evil.csv：净化后落盘必须在 UPLOAD_DIR 内，上级目录无新文件。"""
    result = await data_routes.upload_file(
        FakeUploadFile("../../tmp/evil.csv", b"a,b\n1,2\n"), operator=_operator())
    assert result["ok"] is True, result
    saved = os.listdir(_isolate_upload_dir)
    assert len(saved) == 1
    assert saved[0].endswith("_evil.csv")
    # 逃逸目标（uploads 的上级 tmp_path 及更上层）不得出现该文件
    parent = os.path.dirname(_isolate_upload_dir)
    assert "evil.csv" not in os.listdir(parent)
    assert "evil.csv" not in os.listdir(os.path.dirname(parent))


@pytest.mark.asyncio
async def test_backslash_traversal_never_escapes(_isolate_upload_dir):
    """..\\..\\evil.csv（Windows 反斜杠形态）同样不得逃逸。"""
    result = await data_routes.upload_file(
        FakeUploadFile("..\\..\\evil.csv", b"a,b\n1,2\n"), operator=_operator())
    assert result["ok"] is True, result
    saved = os.listdir(_isolate_upload_dir)
    assert len(saved) == 1 and saved[0].endswith("_evil.csv")
    parent = os.path.dirname(_isolate_upload_dir)
    assert "evil.csv" not in os.listdir(parent)


@pytest.mark.asyncio
async def test_empty_filename_rejected():
    result = await data_routes.upload_file(
        FakeUploadFile("", b"a\n1\n"), operator=_operator())
    assert result["ok"] is False
    assert "不支持的文件格式" in result["error"]


@pytest.mark.asyncio
async def test_extension_whitelist_still_enforced():
    result = await data_routes.upload_file(
        FakeUploadFile("x.txt", b"hello"), operator=_operator())
    assert result["ok"] is False
    assert "不支持的文件格式" in result["error"]


@pytest.mark.asyncio
async def test_normal_csv_still_uploads(_isolate_upload_dir):
    """合法文件名不受影响：落盘在 UPLOAD_DIR 内，返回字段预览。"""
    result = await data_routes.upload_file(
        FakeUploadFile("report.csv", "a,b\n1,2\n".encode("utf-8")),
        operator=_operator())
    assert result["ok"] is True, result
    assert result["total_rows"] == 1
    assert [f["name"] for f in result["fields"]] == ["a", "b"]
    saved = os.listdir(_isolate_upload_dir)
    assert len(saved) == 1 and saved[0].endswith("_report.csv")
    with open(os.path.join(_isolate_upload_dir, saved[0]), "rb") as fh:
        assert fh.read() == b"a,b\n1,2\n"
    os.remove(os.path.join(_isolate_upload_dir, saved[0]))


@pytest.mark.asyncio
async def test_oversized_file_rejected_without_full_buffer(monkeypatch, _isolate_upload_dir):
    """流式读取超限即拒：上限压到 8 字节，100 字节文件不落盘。"""
    monkeypatch.setattr(data_routes, "DATA_UPLOAD_MAX_BYTES", 8)
    result = await data_routes.upload_file(
        FakeUploadFile("big.csv", b"x" * 100), operator=_operator())
    assert result["ok"] is False
    assert "文件过大" in result["error"]
    assert os.listdir(_isolate_upload_dir) == []
