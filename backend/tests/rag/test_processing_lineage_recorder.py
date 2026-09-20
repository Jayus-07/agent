"""索引运行血缘写入协调器测试。"""

from backend.rag.indexing.processing_lineage import ProcessingRunContext
from backend.rag.indexing.processing_lineage_recorder import ProcessingRunRecorder


class _FakeRepository:
    def __init__(self):
        self.created = []
        self.steps = []
        self.finished = []

    def create_run(self, snapshot):
        self.created.append(snapshot)

    def upsert_stage(self, snapshot):
        self.steps.append(snapshot)

    def finish_run(self, run_id, status, **kwargs):
        self.finished.append((run_id, status, kwargs))


class _AsyncFakeRepository(_FakeRepository):
    non_blocking = True


def test_recorder_persists_run_stage_and_terminal_status():
    context = ProcessingRunContext.create("doc-1", "hash-1", "upload")
    repository = _FakeRepository()
    recorder = ProcessingRunRecorder(context, repository)

    recorder.start()
    step_id = context.begin_stage("parser", role=None, engine_type="parser")
    context.finish_stage(step_id, status="success", output_count=2)
    recorder.persist_stage(step_id)
    recorder.finish("success")

    assert repository.created[0].run_id == context.run_id
    assert repository.steps[0].step_id == step_id
    assert repository.finished[0][0:2] == (context.run_id, "success")


def test_non_blocking_repository_keeps_run_order_and_flushes_on_finish():
    context = ProcessingRunContext.create("doc-async", "hash-async", "upload")
    repository = _AsyncFakeRepository()
    recorder = ProcessingRunRecorder(context, repository)

    recorder.start()
    step_id, started_at = recorder.begin_stage(
        "parser", role=None, engine_type="parser"
    )
    recorder.finish_stage(step_id, status="success", started_at=started_at)
    recorder.finish("success")

    assert len(repository.created) == 1
    assert [step.step_id for step in repository.steps] == [step_id]
    assert repository.finished[0][0:2] == (context.run_id, "success")
