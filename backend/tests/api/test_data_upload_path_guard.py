"""data.py /data/upload 路径守卫回归（审计 P0-1，2026-11-03）。

修复前：file.filename 是客户端可控的 multipart 元数据，直接拼进
os.path.join(UPLOAD_DIR, f"{file_id}_{file.filename}")，`../../` 可逃出
UPLOAD_DIR 任意写文件；await file.read() 无大小上限可打满内存。

钉死：
  1. 含路径分隔符（/ 与 \\）的文件名直接拒绝，不在 UPLOAD_DIR 外落盘；
  2. 空名 / 隐藏名（. 开头）/ NUL 文件名拒绝；
  3. 合法 CSV 仍可上传并落盘在 UPLOAD_DIR 内；
  4. 超过大小上限拒绝（流式读取，不再一次性进内存）。

直接调用路由函数（不起 TestClient）：本端点无鉴权依赖注入，
函数级测试即可锁住路径守卫语义。
"""
import os

import pytest

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


def _snapshot_data_dir():
    """UPLOAD_DIR 及其父目录（backend/data）的既有文件名集合。"""
    upload_dir = os.path.realpath(data_routes.UPLOAD_DIR)
    parent = os.path.dirname(upload_dir)
    return {
        upload_dir: set(os.listdir(upload_dir)),
        parent: set(os.listdir(parent)),
    }


def _assert_no_new_files(before):
    upload_dir = os.path.realpath(data_routes.UPLOAD_DIR)
    parent = os.path.dirname(upload_dir)
    after_upload = set(os.listdir(upload_dir))
    after_parent = set(os.listdir(parent))
    assert after_upload == before[upload_dir], "UPLOAD_DIR 不应有新文件"
    assert after_parent == before[parent], "backend/data 不应有新文件"


@pytest.fixture(autouse=True)
def _isolate_upload_dir(monkeypatch, tmp_path):
    """把 UPLOAD_DIR 指到临时目录，避免污染仓库 backend/data。"""
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setattr(data_routes, "UPLOAD_DIR", str(upload_root))
    yield str(upload_root)


@pytest.mark.asyncio
async def test_dotdot_traversal_rejected():
    """../../tmp/evil.csv → 拒绝，且 UPLOAD_DIR 内外都无落盘。"""
    before = _snapshot_data_dir()
    result = await data_routes.upload_file(FakeUploadFile("../../tmp/evil.csv", b"a,b\n1,2\n"))
    assert result["ok"] is False
    assert "非法文件名" in result["error"]
    _assert_no_new_files(before)


@pytest.mark.asyncio
async def test_backslash_traversal_rejected():
    """..\\..\\evil.csv（Windows 反斜杠形态）同样拒绝。"""
    before = _snapshot_data_dir()
    result = await data_routes.upload_file(FakeUploadFile("..\\..\\evil.csv", b"a,b\n1,2\n"))
    assert result["ok"] is False
    assert "非法文件名" in result["error"]
    _assert_no_new_files(before)


@pytest.mark.asyncio
async def test_hidden_and_empty_and_nul_names_rejected():
    for bad in (".hidden.csv", "", "x\x00.csv"):
        result = await data_routes.upload_file(FakeUploadFile(bad, b"a\n1\n"))
        assert result["ok"] is False, f"{bad!r} 应被拒绝"
        assert "非法文件名" in result["error"]


@pytest.mark.asyncio
async def test_none_filename_rejected():
    f = FakeUploadFile("placeholder.csv", b"a\n1\n")
    f.filename = None
    result = await data_routes.upload_file(f)
    assert result["ok"] is False
    assert "非法文件名" in result["error"]


@pytest.mark.asyncio
async def test_normal_csv_still_uploads_inside_dir(_isolate_upload_dir):
    """合法文件名不受影响：落盘在 UPLOAD_DIR 内，返回字段预览。"""
    result = await data_routes.upload_file(
        FakeUploadFile("report.csv", "a,b\n1,2\n".encode("utf-8")))
    assert result["ok"] is True, result
    assert result["filename"] == "report.csv"
    assert result["total_rows"] == 1
    assert [f["name"] for f in result["fields"]] == ["a", "b"]
    saved = os.path.join(_isolate_upload_dir, os.listdir(_isolate_upload_dir)[0])
    assert saved.startswith(os.path.join(_isolate_upload_dir, ""))
    assert saved.endswith("_report.csv")
    with open(saved, "rb") as fh:
        assert fh.read() == b"a,b\n1,2\n"
    os.remove(saved)


@pytest.mark.asyncio
async def test_oversized_file_rejected_without_full_buffer(monkeypatch):
    """流式读取超限即拒：上限压到 8 字节，100 字节文件不落盘。"""
    monkeypatch.setattr(data_routes, "DATA_UPLOAD_MAX_BYTES", 8)
    before = _snapshot_data_dir()
    result = await data_routes.upload_file(
        FakeUploadFile("big.csv", b"x" * 100))
    assert result["ok"] is False
    assert "文件过大" in result["error"]
    _assert_no_new_files(before)
