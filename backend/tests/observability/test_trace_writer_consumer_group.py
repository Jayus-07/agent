"""D-3：Trace Redis Stream 必须由 Consumer Group 消费并在落库后 ACK。"""
import json


class _FakeRedis:
    def __init__(self):
        self.group_creates = []
        self.reads = []
        self.acks = []

    def xgroup_create(self, stream, group, id="0", mkstream=False):
        self.group_creates.append((stream, group, id, mkstream))

    def xreadgroup(self, group, consumer, streams, count=None, block=0):
        self.reads.append((group, consumer, streams, count, block))
        return [(next(iter(streams)), [("1-0", {"data": json.dumps({"id": "trace-1"})})])]

    def xack(self, stream, group, message_id):
        self.acks.append((stream, group, message_id))


class _Store:
    def __init__(self):
        self.saved = []

    def save_dict(self, data):
        self.saved.append(data)


def test_trace_writer_uses_consumer_group_and_acks_after_persist(monkeypatch):
    from backend.observability import trace_writer as module

    redis = _FakeRedis()
    trace_store = _Store()
    analytics_store = _Store()
    monkeypatch.setattr(
        module.TraceWriteQueue,
        "_capture_stores",
        staticmethod(lambda: (trace_store, analytics_store)),
    )
    monkeypatch.setattr(
        "backend.observability.pg_trace_sink.write_trace",
        lambda _data: None,
    )

    queue = module.TraceWriteQueue.__new__(module.TraceWriteQueue)
    queue._redis = redis
    queue._use_redis = True
    queue._group_ready = False
    queue._consumer_group = "trace-writers"
    queue._consumer_name = "test-consumer"
    queue._local_queue = None

    batch = queue._read_batch()
    assert redis.group_creates == [
        ("agent:trace:write", "trace-writers", "0", True)
    ]
    assert redis.reads[0][0] == "trace-writers"
    assert redis.reads[0][2] == {"agent:trace:write": ">"}
    assert batch[0][2] == "1-0"

    queue._flush_batch(batch)

    assert trace_store.saved == [{"id": "trace-1"}]
    assert redis.acks == [("agent:trace:write", "trace-writers", "1-0")]
