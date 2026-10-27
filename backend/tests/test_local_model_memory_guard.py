"""参考本地模型服务的有界推理与内存失败行为，不加载模型权重。"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import threading

import pytest
from fastapi import HTTPException


@pytest.fixture
def server():
    path = Path(__file__).resolve().parents[2] / 'scripts' / 'local_model_server.py'
    spec = importlib.util.spec_from_file_location('memory_guard_model_server', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def test_busy_requests_rejected_without_waiting_or_model_allocation(server):
    assert server._INFERENCE_CAPACITY.acquire(blocking=False)
    assert server._INFERENCE_CAPACITY.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as caught:
            with server._inference_slot():
                pytest.fail('没有空闲名额时不可进入推理')
        assert caught.value.status_code == 503
        assert caught.value.detail == 'model_busy'
    finally:
        server._INFERENCE_CAPACITY.release()
        server._INFERENCE_CAPACITY.release()


def test_model_oom_releases_slot_and_enters_cooldown(server, monkeypatch):
    class Oom(RuntimeError):
        pass

    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(
        OutOfMemoryError=Oom, is_available=lambda: False,
    )))
    with pytest.raises(HTTPException) as caught:
        with server._inference_slot():
            raise Oom('模拟显存不足')
    assert caught.value.detail == 'model_memory_exhausted'
    assert not server._INFERENCE_LOCK.locked()
    with pytest.raises(HTTPException, match='model_memory_cooldown'):
        with server._inference_slot():
            pytest.fail('冷却期间不应再次调用模型')
    assert server._INFERENCE_CAPACITY.acquire(blocking=False)
    assert server._INFERENCE_CAPACITY.acquire(blocking=False)


def test_embedding_batch_is_bounded_without_changing_vector_output(server, monkeypatch):
    calls = []
    vectors = [[0.1, 0.2], [0.3, 0.4]]

    def encode(texts, **kwargs):
        calls.append((texts, kwargs))
        return SimpleNamespace(tolist=lambda: vectors)

    monkeypatch.setattr(server, '_get_embedder', lambda: SimpleNamespace(encode=encode))
    result = server.embeddings(server.EmbeddingsRequest(input=['甲', '乙']))
    assert calls == [(['甲', '乙'], {'batch_size': 1, 'normalize_embeddings': True})]
    assert [row['embedding'] for row in result['data']] == vectors
    with pytest.raises(HTTPException) as caught:
        server.embeddings(server.EmbeddingsRequest(input=['甲'] * 65))
    assert caught.value.status_code == 413 and len(calls) == 1


def test_waiter_rechecks_cooldown_after_previous_inference_oom(server, monkeypatch):
    released = []

    def acquire(**kwargs):
        server._OOM_UNTIL = server.time.monotonic() + 3
        return True

    monkeypatch.setattr(server, '_INFERENCE_LOCK', SimpleNamespace(
        acquire=acquire, release=lambda: released.append(True),
    ))
    with pytest.raises(HTTPException) as caught:
        with server._inference_slot():
            pytest.fail('等待者拿到锁后仍须遵守前一个请求触发的冷却')
    assert caught.value.detail == 'model_memory_cooldown'
    assert released == [True]
    assert server._INFERENCE_CAPACITY.acquire(blocking=False)
    assert server._INFERENCE_CAPACITY.acquire(blocking=False)
