"""export_csv_tool filename 约束（2026-09-23 D1-2 回归）。

filename 是 LLM 可控参数，此前直接拼 `export_dir / f"{filename}.csv"`，
`../../x` 即可写出目录树外任意 .csv（路径穿越写文件）。锁定
_safe_export_path 的白名单/拒绝/越界防线与审批后路径的消息语义。
"""
import pytest

from backend.tools.export import _safe_export_path


@pytest.fixture()
def export_dir(tmp_path, monkeypatch):
    import backend.config as config

    base = tmp_path / "storage" / "docs"
    monkeypatch.setattr(config, "STORAGE_DOCS_DIR", str(base))
    return (base / "exports").resolve()


@pytest.mark.parametrize("name", ["report", "sales_2026", "季度报表",
                                  "user-behavior", "a" * 100])
def test_legal_names_resolve_inside_export_dir(export_dir, name):
    path = _safe_export_path(name)
    assert path.is_relative_to(export_dir)
    assert path.name == f"{name}.csv"


def test_empty_name_falls_back_to_timestamp_default(export_dir):
    path = _safe_export_path("")
    assert path.is_relative_to(export_dir)
    assert path.name.startswith("export_") and path.name.endswith(".csv")


def test_csv_suffix_is_stripped_server_appends_extension(export_dir):
    assert _safe_export_path("foo.csv").name == "foo.csv"


@pytest.mark.parametrize("bad", [
    "../../tmp/x",
    "..\\..\\x",
    "/a/b",
    "C:\\temp\\x",
    "foo/../bar",
    "..",
    "a/b",
    "with space",
    "with.dot",
    "../escape.csv",
])
def test_traversal_and_illegal_chars_rejected(export_dir, bad):
    with pytest.raises(ValueError):
        _safe_export_path(bad)


def test_reject_surfaces_as_export_failed_message(export_dir, monkeypatch):
    """审批后执行路径：非法 filename 以 [EXPORT FAILED] 文案返回而非异常。"""
    from backend.tools import export as export_mod

    class _Res:
        status = "success"
        rows = [{"a": 1}]
        columns = ["a"]
        error = ""

    class _Agent:
        def ask_struct(self, question, policy=None):
            return _Res()

    monkeypatch.setattr(export_mod, "_get_sql_agent", lambda: _Agent())
    out = export_mod._export_csv_after_approval("q", "../../evil")
    assert out.startswith("[EXPORT FAILED]")
