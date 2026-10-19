"""记忆检索的同步 embedding 不得阻塞后台 asyncio loop。"""

import asyncio
import threading
import time

from backend.memory.service import _embed_query_off_loop


def test_sync_embedding_runs_off_memory_event_loop():
    main_thread = threading.get_ident()
    started = threading.Event()
    finished = threading.Event()
    embedding_threads = []

    class BlockingEmbedding:
        def embed_query(self, query):
            embedding_threads.append(threading.get_ident())
            started.set()
            time.sleep(0.2)
            finished.set()
            return [float(len(query))]

    async def scenario():
        task = asyncio.create_task(
            _embed_query_off_loop(BlockingEmbedding(), "test query")
        )
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 1), 1)
        # 若同步 Provider 调用占住 loop，这个协程无法在 embedding 返回前完成。
        await asyncio.wait_for(asyncio.sleep(0), 0.02)
        assert not finished.is_set()
        return await task

    assert asyncio.run(scenario()) == [10.0]
    assert embedding_threads and embedding_threads[0] != main_thread
