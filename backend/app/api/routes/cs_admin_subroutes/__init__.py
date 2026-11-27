"""CS Admin 子路由包聚合入口。"""
from __future__ import annotations

from backend.app.api.routes.cs_admin_subroutes.base import (
    FacadeProxy,
    _CS_OPERATOR_ROLES,
    facade,
    logger,
)
from backend.app.api.routes.cs_admin_subroutes.schemas import *
from backend.app.api.routes.cs_admin_subroutes.deps import *
from backend.app.api.routes.cs_admin_subroutes.confirm import *
from backend.app.api.routes.cs_admin_subroutes.conversations import *
from backend.app.api.routes.cs_admin_subroutes.handoff import *
from backend.app.api.routes.cs_admin_subroutes.user_messages import *
