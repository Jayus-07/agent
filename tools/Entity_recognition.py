
from config import KNOWN_PERSON_NAMES, PERSON_QUERY_PATTERNS


# =========================
# 1 主入口
# =========================
def extract_person_names(text: str):
    names = set()

    # =================================================
    # 1. 词典匹配（只在文本中实际出现时才添加）
    # =================================================
    for known_name in KNOWN_PERSON_NAMES:
        if known_name in text:
            names.add(known_name)

    return list(names)  # 转换为 list，ChromaDB 不支持 set


def is_person_query(question: str) -> bool:
    """
    判断是否属于人物查询
    """

    return any(
        p in question
        for p in PERSON_QUERY_PATTERNS
    )




