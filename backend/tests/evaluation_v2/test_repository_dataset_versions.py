from contextlib import contextmanager

from backend.evaluation_v2 import repository


def test_list_dataset_versions_includes_dataset_id_for_suite_binding(monkeypatch):
    dataset_id = "8b9bc68a-8d61-45c3-a093-94a8ddadbd61"
    version_id = "fe0de9b3-6f14-478c-a645-57d42bf72ed0"

    class Cursor:
        def __init__(self):
            self.queries = []
            self.fetchone_calls = 0

        def execute(self, query, _params=None):
            self.queries.append(query)

        def fetchone(self):
            self.fetchone_calls += 1
            return {"dataset_id": dataset_id}

        def fetchall(self):
            return [{
                "dataset_version_id": version_id,
                "dataset_id": dataset_id,
                "version": "1.0.0",
                "status": "published",
                "content_hash": "a" * 64,
                "published_by": "user:1",
                "published_at": None,
                "created_at": None,
                "case_count": 4,
            }]

    cursor = Cursor()

    @contextmanager
    def transaction(_tenant_id):
        yield None, cursor

    monkeypatch.setattr(repository, "_transaction", transaction)

    versions = repository.list_dataset_versions("tenant-a", dataset_id)

    assert cursor.fetchone_calls == 1
    assert "v.dataset_id" in cursor.queries[1]
    assert versions == [{
        "dataset_version_id": version_id,
        "dataset_id": dataset_id,
        "version": "1.0.0",
        "status": "published",
        "content_hash": "a" * 64,
        "published_by": "user:1",
        "published_at": None,
        "created_at": None,
        "case_count": 4,
    }]
