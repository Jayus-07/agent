"""宿主客服 live 评测入口；复用既有 CLI、Runner、报告与计费链路。"""

import asyncio
import json
import os
from pathlib import Path
import sys
from urllib.request import ProxyHandler, build_opener


def prepare_environment() -> None:
    """宿主只消费共享 RAG 服务，不加载本地空索引。"""
    os.environ['RAG_MODE'] = 'remote'
    os.environ['RAG_SERVICE_URL'] = 'http://127.0.0.1:8090'
    os.environ['BUSINESS_SERVICE_URL'] = 'http://127.0.0.1:8081'
    os.environ['NO_PROXY'] = '*'
    os.environ['CS_REFUND_REQUEST_MODE'] = 'disabled'


def check_services() -> None:
    """付费调用前检查本机模型和 RAG 就绪；失败直接中止。"""
    opener = build_opener(ProxyHandler({}))
    with opener.open('http://127.0.0.1:8088/healthz', timeout=5) as response:
        models = json.load(response)
    if models.get('status') != 'ok' or not {'embedder', 'reranker'}.issubset(
        models.get('models_loaded', [])
    ):
        raise RuntimeError('本地 embedding/reranker 尚未就绪')
    with opener.open('http://127.0.0.1:8090/readyz', timeout=5) as response:
        rag = json.load(response)
    with opener.open('http://127.0.0.1:8081/health', timeout=5) as response:
        if response.status != 200:
            raise RuntimeError('本机业务服务尚未就绪')
    if rag.get('ready') is not True:
        raise RuntimeError('共享 RAG 服务尚未就绪')


def main() -> None:
    prepare_environment()
    check_services()
    from backend.infra.llm.registry_store import refresh_registry

    if not asyncio.run(refresh_registry()):
        raise RuntimeError('模型注册表刷新失败，已中止评测')
    from backend.evaluation.cli import main as evaluate

    sys.argv = [sys.argv[0], 'cs', '--live', *sys.argv[1:]]
    evaluate()


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
