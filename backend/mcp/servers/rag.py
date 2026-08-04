"""RAG MCP Server — 知识库检索能力"""
from backend.mcp.manager import MCPServer
from backend.rag.pipeline import get_rag_pipeline


class RAGMCPServer(MCPServer):
    """知识库检索：search/list_documents/get_stats。"""
    name = "rag"
    description = "跨境电商知识库检索（RAG）"

    def list_tools(self) -> list:
        return [
            {
                "name": "search_knowledge",
                "description": "从知识库检索 + LLM 生成回答",
                "parameters": {
                    "question": {"type": "string", "required": True, "description": "用户问题"},
                    "kb_id": {"type": "string", "required": False, "default": "default", "description": "知识库 ID"},
                },
            },
            {
                "name": "list_documents",
                "description": "列出知识库中的文档",
                "parameters": {
                    "keyword": {"type": "string", "required": False, "default": "", "description": "搜索关键词"},
                    "page": {"type": "integer", "required": False, "default": 1, "description": "页码"},
                },
            },
            {
                "name": "get_stats",
                "description": "知识库统计信息",
                "parameters": {},
            },
        ]

    def call_tool(self, tool_name: str, params: dict):
        if tool_name == "search_knowledge":
            pipeline = get_rag_pipeline()
            answer = pipeline.ask(
                question=params["question"],
                kb_id=params.get("kb_id", "default"),
            )
            return {"answer": answer}

        if tool_name == "list_documents":
            from backend.rag.indexing.doc_registry import DocumentRegistry
            from backend.config import DOC_REGISTRY_PATH
            registry = DocumentRegistry(DOC_REGISTRY_PATH)
            keyword = params.get("keyword", "")
            page = params.get("page", 1)
            page_size = 20
            all_docs = registry.search(keyword=keyword) if keyword else registry.list_active()
            total = len(all_docs)
            start = (page - 1) * page_size
            docs = all_docs[start:start + page_size]
            return {"documents": docs, "total": total, "page": page, "page_size": page_size}

        if tool_name == "get_stats":
            from backend.config import CHROMA_PATH, DOC_DB_PATH, EMBEDDING_MODEL_PATH
            from pathlib import Path
            return {
                "chroma_path": CHROMA_PATH,
                "chroma_exists": Path(CHROMA_PATH).exists(),
                "doc_db_path": DOC_DB_PATH,
                "doc_db_exists": Path(DOC_DB_PATH).exists(),
                "embedding_model": EMBEDDING_MODEL_PATH,
            }

        raise ValueError(f"未知 tool: {tool_name}")