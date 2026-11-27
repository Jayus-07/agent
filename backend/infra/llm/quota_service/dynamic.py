from __future__ import annotations

import sys
from typing import Any


def _facade():
    return sys.modules.get("backend.infra.llm.quota")


def current_budget_periods(*args: Any, **kwargs: Any):
    f = _facade()
    fn = getattr(f, "budget_periods", None) if f else None
    if fn is None:
        from .policies import budget_periods
        fn = budget_periods
    return fn(*args, **kwargs)


def current_resolve_budget_policies(*args: Any, **kwargs: Any):
    f = _facade()
    fn = getattr(f, "resolve_budget_policies", None) if f else None
    if fn is None:
        from .policies import resolve_budget_policies
        fn = resolve_budget_policies
    return fn(*args, **kwargs)


def current_record_quota_metric(*args: Any, **kwargs: Any):
    f = _facade()
    fn = getattr(f, "_record_quota_metric", None) if f else None
    if fn is None:
        from .metrics import _record_quota_metric
        fn = _record_quota_metric
    return fn(*args, **kwargs)


def current_insert_threshold_events(*args: Any, **kwargs: Any):
    f = _facade()
    fn = getattr(f, "_insert_threshold_events", None) if f else None
    if fn is None:
        from .metrics import _insert_threshold_events
        fn = _insert_threshold_events
    return fn(*args, **kwargs)
