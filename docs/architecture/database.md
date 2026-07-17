# 数据库规则

## 统一分层

```
Repository → Service → API
```

## Repository

负责：
- SQL
- ORM 查询
- 数据持久化

**不含业务逻辑**

## Service

负责：
- 业务规则
- 事务边界
- 权限校验

## API

负责：
- 参数校验
- 调用 Service
- 返回 DTO

## 禁止

```
API → SQLAlchemy Session → SQL  （跳过 Repository/Service）
```

## SQL

- 参数化查询
- 禁止字符串拼接