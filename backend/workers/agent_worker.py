"""workers/agent_worker.py — Agent Worker 进程入口（Celery prefork）。

启动方式（与 API 进程完全解耦，可独立横向扩展）：

    # 本机（Redis/PG 走宿主机网络）
    celery -A backend.workers.agent_worker worker --loglevel=info -c 4 -Q agent -n agent-worker1@%h

    # 容器内（compose 网络走服务名）
    celery -A backend.workers.agent_worker worker --loglevel=info -c 4 -Q agent

    # Kubernetes：每个副本 = 一个 Deployment（见改造报告的 K8s 扩展方案），
    # 同一 -Q agent 队列自动负载均衡，副本数即吞吐水平扩展。

并发说明：LangGraph 节点内部有同步阻塞（LLM/DB），prefork 进程池（-c）
比线程池更适合隔离；单 Worker 内 -c 4 即可同时跑 4 个任务，排队靠 Redis。
"""
from backend.tasks.agent_tasks import execute_agent_task  # noqa: F401 触发任务注册
from backend.tasks.celery_app import celery_app

__all__ = ["celery_app", "execute_agent_task"]
