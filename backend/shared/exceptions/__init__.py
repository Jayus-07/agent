"""shared/exceptions — 业务异常基类

按 CLAUDE.md '业务层使用自定义异常'，所有业务异常应继承 AppError。
当前已存在 4 个分散的自定义异常（不动它们）：
  - backend/shared/monitoring/timeout.py:    TimeoutError
  - backend/sql/row_security.py:            RowSecurityError
  - backend/sql/sql_validator.py:           ValidationError
  - backend/app/api/schemas.py:            ErrorResponse (Pydantic 模型)

未来新增异常时:
  1. 继承 AppError
  2. 设置 status_code 和 error_code
  3. 在 backend/app/core/exceptions.py 添加对应 handler

使用示例:
    from backend.shared.exceptions import AppError

    class DocumentNotFound(AppError):
        status_code = 404
        error_code = "DOCUMENT_NOT_FOUND"

        def __init__(self, doc_id: str):
            super().__init__(f"文档 {doc_id} 不存在")
            self.doc_id = doc_id
"""


class AppError(Exception):
    """业务异常基类。

    业务层抛出此类异常，由 FastAPI exception_handler 统一转换为 HTTP 响应。
    不需要在业务代码中捕获并转换。
    """
    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, message: str = "", **context):
        super().__init__(message)
        self.message = message
        self.context = context  # 上下文信息（如 doc_id、session_id 等）


__all__ = ["AppError"]