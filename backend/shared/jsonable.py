"""shared/jsonable.py — 把任意对象转为 JSON 安全格式。

P2:从 evaluation/runners/builtin.py:_safe_jsonable 抽出,让报告生成器等场景复用。

设计:
  - 深度递归 dict / list / tuple
  - str/int/float/bool/None 直通
  - 其它对象(自定义类 / numpy / set / LangChain Document / Chroma metadata)
    → 尝试 str() 截断 500 字符;失败返 None
  - 防御性:任何层级异常都不传播,保证序列化永不掉链
"""


def safe_jsonable(obj, max_str_len: int = 500) -> object:
    """递归把任意对象转为 JSON 安全格式(str/int/float/bool/None/list/dict)。

    Args:
        obj: 任意 Python 对象(LangChain Document / numpy ndarray / 自定义类 / set 等)
        max_str_len: 不可序列化对象转 str 时最大长度,超过截断。

    Returns:
        JSON 可序列化的 Python 原生类型。失败的层级返 None,不抛异常。

    Examples:
        >>> safe_jsonable({"a": [1, 2, "中文"]})
        {'a': [1, 2, '中文']}
        >>> safe_jsonable(some_doc_with_circular_ref)  # 不死循环
        '...truncated string...'
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [safe_jsonable(x, max_str_len) for x in obj]
    if isinstance(obj, dict):
        return {str(k): safe_jsonable(v, max_str_len) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return [safe_jsonable(x, max_str_len) for x in obj]
    # 其它: LangChain Document / numpy / 自定义对象 → 截断字符串
    try:
        s = str(obj)
        return s if len(s) <= max_str_len else s[:max_str_len] + "..."
    except Exception:
        return None


__all__ = ["safe_jsonable"]