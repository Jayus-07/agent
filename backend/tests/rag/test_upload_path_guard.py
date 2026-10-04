"""上传测试产物路径门禁。"""


def test_rejects_temp_pytest_and_agent_fixture_components():
    from backend.rag.indexing.upload_path_guard import test_artifact_path_reason

    assert test_artifact_path_reason(r"C:\Users\wh\AppData\Local\Temp\a.md")
    assert test_artifact_path_reason("/tmp/agent_test_data_1/doc.md")
    assert test_artifact_path_reason("C:/pytest-123/work.md")


def test_allows_production_paths_and_similar_words_in_a_filename():
    from backend.rag.indexing.upload_path_guard import test_artifact_path_reason

    assert test_artifact_path_reason("/srv/agent/docs/general/temporary_report.md") is None
    assert test_artifact_path_reason("/srv/agent/docs/general/tmp_report.md") is None


def test_upload_impl_returns_422_for_test_artifact_filename():
    import asyncio

    from backend.app.api.routes.rag_upload import sync_upload_impl

    class Upload:
        filename = r"C:\Temp\agent_test_data_1.md"

    result = asyncio.run(sync_upload_impl(
        Upload(), None, 1024, "/unused", 1024, 1024, 1000,
    ))
    assert result["ok"] is False
    assert result["status_code"] == 422
