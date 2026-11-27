from __future__ import annotations

import os
from contextlib import contextmanager

from backend.shared.logger import logger


class QuotaStoreBase:
    """PG 预算存储基础连接与生命周期管理。"""

    def __init__(self, connection_factory=None):
        self._connection_factory = connection_factory or self._connect
        prefix = os.getenv("BUDGET_PG_TABLE_PREFIX", "")
        self._policies = prefix + "budget_policies"
        self._ledger = prefix + "budget_ledger"
        self._reservations = prefix + "budget_reservations"
        self._events = prefix + "budget_events"
        self._price_table = prefix + "model_price"
        self._policy_audit = prefix + "budget_policy_audit"
        # 第二批：待对账核销动作审计（append-only，144 迁移）
        self._resolution_audit = prefix + "budget_reservation_resolutions"

    @staticmethod
    @contextmanager
    def _connect():
        """池化连接（统一 Engine）：块结束自动 commit、异常 rollback、归还池。

        语义对齐 psycopg2 原生 ``with conn``（块结束 commit / 异常 rollback），
        但连接归还池，不再每次新建。
        """
        from backend.config.database import MEMORY_DB_CONFIG  # noqa: F401  (engine_for 归一)

        from backend.infra.db import engine_for

        conn = engine_for(MEMORY_DB_CONFIG).raw_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
