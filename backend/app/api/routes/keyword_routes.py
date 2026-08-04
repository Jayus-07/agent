"""关键词规则管理 API"""
from fastapi import APIRouter, Request

router = APIRouter(prefix="/rag/keywords", tags=["关键词管理"])


@router.get("")
async def list_keywords(doc_type: str = "", category: str = "", search: str = "", enabled: str = ""):
    from backend.rag.preprocessing.keyword_store import get_keyword_store
    en = None if enabled == "" else int(enabled)
    return {"items": get_keyword_store().list_all(doc_type=doc_type, category=category,
                                                   enabled=en, search=search)}


@router.get("/doc-types")
async def list_doc_types():
    from backend.rag.preprocessing.keyword_store import get_keyword_store
    return {"doc_types": get_keyword_store().list_doc_types()}


@router.get("/categories")
async def list_keyword_categories():
    from backend.rag.preprocessing.keyword_store import get_keyword_store
    return {"categories": get_keyword_store().list_categories()}


@router.post("")
async def upsert_keyword(req: Request):
    from backend.rag.preprocessing.keyword_store import get_keyword_store
    body = await req.json()
    return get_keyword_store().upsert(
        keyword=body.get("keyword", "").strip(),
        doc_type=body.get("doc_type", "general"),
        category=body.get("category", ""),
        weight=int(body.get("weight", 1)),
        enabled=int(body.get("enabled", 1)),
    )


@router.post("/batch")
async def batch_upsert_keywords(req: Request):
    from backend.rag.preprocessing.keyword_store import get_keyword_store
    body = await req.json()
    return get_keyword_store().batch_upsert(body.get("items", []))


@router.delete("/{keyword}")
async def delete_keyword(keyword: str):
    from backend.rag.preprocessing.keyword_store import get_keyword_store
    return get_keyword_store().delete(keyword)


@router.put("/{keyword}/toggle")
async def toggle_keyword(keyword: str, req: Request):
    from backend.rag.preprocessing.keyword_store import get_keyword_store
    body = await req.json()
    return get_keyword_store().toggle(keyword, int(body.get("enabled", 1)))
