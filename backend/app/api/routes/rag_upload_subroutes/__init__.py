"""RAG Upload 子路由包聚合入口。"""
from __future__ import annotations

from backend.app.api.routes.rag_upload_subroutes.base import *
from backend.app.api.routes.rag_upload_subroutes.lock import *
from backend.app.api.routes.rag_upload_subroutes.validation import *
from backend.app.api.routes.rag_upload_subroutes.cleanup import *
from backend.app.api.routes.rag_upload_subroutes.progress import *
from backend.app.api.routes.rag_upload_subroutes.indexing import *
from backend.app.api.routes.rag_upload_subroutes.failures import *
from backend.app.api.routes.rag_upload_subroutes.routes import router
