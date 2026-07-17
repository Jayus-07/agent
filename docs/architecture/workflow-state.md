# Workflow State 规则

## 统一使用

- TypedDict
- Pydantic

## 正确

```python
class WorkflowState(TypedDict):
    question: str
    step_results: list
```

## 禁止

```python
state[dynamic_key] = value  # 动态增加未知字段
```

## 要求

- 可预测
- 可序列化