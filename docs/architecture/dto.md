# DTO 规则

## 正确分层

```
ORM → Domain → DTO → API Response
```

## DTO 禁止包含

- SQLAlchemy Session
- ORM Model 实例
- 数据库连接
- 业务逻辑
- 副作用方法

## DTO 只做

数据传输 + 序列化/反序列化。