"""Seed Data Framework — 跨境电商业务数据生成框架。

用法:
    python -m seed_data --profile mvp --export json
    python -m seed_data --profile tiny --export dict

架构:
    Generator (原始数据) → Factory (实体组装) → Context (注册 + FK 解析)
    → Exporter (输出适配: JSON / PostgreSQL / Python dict)
    → Validator (引用完整性 / 业务规则 / 数量级校验)
"""

__version__ = "0.1.0"
