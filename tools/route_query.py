def route_query(query: str) -> str:
    q = query.lower()

    doc_level_keywords = [
        "做过什么", "是谁", "介绍", "总结", "经历", "是谁", "简历"
    ]

    if any(k in q for k in doc_level_keywords):
        return "doc"

    return "chunk"


