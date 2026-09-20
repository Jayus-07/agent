"""处理血缘并发隔离和幂等写入测试。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from backend.rag.indexing.processing_lineage import ProcessingRunContext
from backend.rag.indexing.processing_lineage_recorder import ProcessingRunRecorder


class _ConcurrentRepository:
    def __init__(self):
        self._lock = Lock()
        self.runs = []
        self.steps = []
        self.finished = []

    def create_run(self, snapshot):
        with self._lock:
            self.runs.append(snapshot)

    def upsert_stage(self, snapshot):
        with self._lock:
            self.steps.append(snapshot)

    def finish_run(self, run_id, status, **kwargs):
        with self._lock:
            self.finished.append((run_id, status))


def test_one_hundred_runs_keep_isolated_ids_and_terminal_state():
    repository = _ConcurrentRepository()

    def run_one(index: int):
        context = ProcessingRunContext.create(
            f"doc-{index}", f"hash-{index}", "upload", batch_id="batch-1"
        )
        recorder = ProcessingRunRecorder(context, repository)
        recorder.start()
        step_id, started_at = recorder.begin_stage(
            "parser", role=None, engine_type="parser"
        )
        recorder.finish_stage(
            step_id, status="success", started_at=started_at, output_count=1
        )
        recorder.finish("success")
        return context.run_id, step_id

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(run_one, range(100)))

    run_ids = {run_id for run_id, _ in results}
    step_ids = {step_id for _, step_id in results}
    assert len(run_ids) == 100
    assert len(step_ids) == 100
    assert len(repository.runs) == 100
    assert len(repository.steps) == 100
    assert len(repository.finished) == 100
    assert {status for _, status in repository.finished} == {"success"}
