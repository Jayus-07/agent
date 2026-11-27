from __future__ import annotations

from .store_base import QuotaStoreBase
from .store_policies import QuotaPolicyStoreMixin
from .store_reservations import QuotaReservationStoreMixin
from .store_reconciliation import QuotaReconciliationStoreMixin


class PostgresQuotaStore(
    QuotaPolicyStoreMixin,
    QuotaReservationStoreMixin,
    QuotaReconciliationStoreMixin,
    QuotaStoreBase,
):
    """PG 原子预占/结算存储；额度表是跨进程权威。"""
    pass
