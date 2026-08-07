"""测试 require_rate_limit dependency 的 FastAPI 集成。

Why:
  防止 require_rate_limit 的 request 参数再次丢失 : Request 类型注解，
  导致 SSE 422 (query,request Field required) bug 复发。
"""
from fastapi import FastAPI, Depends, Request
from fastapi.testclient import TestClient

from backend.infra.llm.rate_limiter import require_rate_limit


def test_request_param_has_type_annotation():
    """防止回归：require_rate_limit 的 request 必须有 : Request 类型注解。

    缺失会导致 FastAPI 把 request 当 query string 处理，
    OpenAPI 生成 "name=request, in=query, required=true" 错误 schema。
    """
    import inspect

    sig = inspect.signature(require_rate_limit)
    request_param = sig.parameters.get("request")
    assert request_param is not None, "request 参数不存在"
    # annotation 必须不是 inspect.Parameter.empty
    assert request_param.annotation is not inspect.Parameter.empty, (
        "❌ request 参数缺少类型注解！FastAPI 会把它当 query string 处理，"
        "导致 SSE 端点 422 'Field required loc=query.request'"
    )


def test_rate_limit_dependency_registered_as_body_param():
    """集成测试：rate_limit 的 request 不能注册成 required query 参数。

    修复前：require_rate_limit(request, ...) 缺 : Request → FastAPI 生成
        {"name":"request","in":"query","required":true}
    修复后：user_id 仍是可选 query，但 request 不再出现在 parameters 里。
    """
    app = FastAPI()

    @app.post("/test-stream")
    async def test_endpoint(r: Request, _rate=Depends(require_rate_limit)):
        return {"ok": True}

    schema = app.openapi()
    params = schema["paths"]["/test-stream"]["post"]["parameters"]

    # 关键断言：不能有 required query 参数（那是 SSE 422 的根因）
    required_query = [p for p in params if p["in"] == "query" and p.get("required", False)]
    assert len(required_query) == 0, (
        f"❌ rate_limit 的 request 仍被注册成 required query 参数: {required_query}"
    )

    # 顺便断言：没有名为 request 的参数（说明 Request 类型注解生效）
    param_names = [p["name"] for p in params]
    assert "request" not in param_names, (
        f"❌ 名为 request 的 query 参数仍存在: {param_names}"
    )
