"""本机部署调度纪律的配置契约（可靠性盘点 D8 缺口补齐）。

beat 单实例与四类 worker 并发上限此前只是 docker-compose.yml 注释纪律，
无任何自动化断言。这里把纪律固化成可回归的契约测试：
- 四类 worker 的 --concurrency 默认值必须保持既定档位；
- beat 服务必须单实例（不得配置 replicas>1）且保留 PID1 argv 健康检查。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_worker_concurrency_defaults_follow_scheduling_contract():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for expected in (
        '"--concurrency=${AGENT_WORKER_CONCURRENCY:-4}"',
        '"--concurrency=${RAG_INDEX_WORKER_CONCURRENCY:-2}"',
        '"--concurrency=${MAINTENANCE_WORKER_CONCURRENCY:-1}"',
        '"--concurrency=${REPORT_WORKER_CONCURRENCY:-1}"',
    ):
        assert expected in text, f"compose 缺少既定并发档位: {expected}"


def test_beat_is_single_instance_with_pid_healthcheck():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    marker = "\n  beat:\n"
    assert marker in text, "docker-compose.yml 缺少 beat 服务定义"
    # beat 块 = 从服务定义到下一个同级服务键（恰好两空格缩进的键行）
    block = text.split(marker, 1)[1]
    m = re.search(r"\n  [A-Za-z0-9_-]+:", block)
    block = block[: m.start()] if m else block
    assert "replicas:" not in block, "beat 不允许配置多副本（扩容会重复调度）"
    assert "healthcheck:" in block, "beat 必须保留 PID1 argv 健康检查"
