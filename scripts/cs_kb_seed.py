"""客服知识库种子契约：种子清单 + git 源目录同步（纯 stdlib，可单测）。

事实源链路：git 内 docs/customer-service/kb/{kb_id}/{fname}（Dockerfile
scoped COPY 进镜像）→ sync_kb_seed_files() 同步到
{DOCS_DIRECTORY}/{kb_id}/customer/{fname}（持久化数据卷）→
index_cs_seed_docs.py 增量索引。

B1（cs-capability-gap，2026-11-04）：cs_policy 8 篇政策文档 +
cs_faq 高频速查，消灭此前人工 docker cp 送文档的步骤。
"""

from __future__ import annotations

import os
import shutil

# (kb_id, 文件名)：kb_id 同时是 DOCS_DIRECTORY 与 git 源目录下的子目录名
SEED_FILES: list[tuple[str, str]] = [
    ("cs_faq", "faq-service-general.md"),
    ("cs_faq", "faq-after-sales-process.md"),
    ("cs_product", "faq-product-usage.md"),
    ("cs_product", "product-catalog.md"),
    ("cs_complaint", "process-complaint-handling.md"),
    ("cs_scripts", "scripts-standard.md"),
    ("cs_aftersales", "faq-exchange-repair.md"),
    ("cs_aftersales", "faq-after-sales-process.md"),
    # B1：政策底座（cs_policy 首次有种子）+ 高频速查
    ("cs_policy", "refund-policy.md"),
    ("cs_policy", "price-match-policy.md"),
    ("cs_policy", "coupon-policy.md"),
    ("cs_policy", "points-policy.md"),
    ("cs_policy", "payment-faq-policy.md"),
    ("cs_policy", "shipping-policy.md"),
    ("cs_policy", "invoice-policy.md"),
    ("cs_policy", "aftersales-details.md"),
    ("cs_faq", "faq-quick-reference.md"),
]

# git 内知识库源目录（相对仓库根；容器内 = /app/docs/customer-service/kb）
CS_KB_SOURCE_SUBPATH = os.path.join("docs", "customer-service", "kb")


def repo_root_source_dir(repo_root: str) -> str:
    """仓库根 → git 内知识库源目录绝对路径。"""
    return os.path.join(repo_root, CS_KB_SOURCE_SUBPATH)


def sync_kb_seed_files(
    source_root: str,
    docs_directory: str,
    seed_files: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """把 git 源文档同步到 DOCS_DIRECTORY 的种子约定路径。

    对每个 (kb_id, fname)：源文件 {source_root}/{kb_id}/{fname} 存在时
    覆盖拷贝到 {docs_directory}/{kb_id}/customer/{fname}（customer 子目录
    自动创建）。源缺失的条目跳过——兼容早期仅存在于数据卷、无 git 源的
    历史 8 篇（由索引步骤按文件存在性决定去留）。

    返回实际拷贝的 (kb_id, fname) 列表（按 seed_files 顺序）。
    """
    copied: list[tuple[str, str]] = []
    for kb_id, fname in seed_files:
        src = os.path.join(source_root, kb_id, fname)
        if not os.path.isfile(src):
            continue
        dst_dir = os.path.join(docs_directory, kb_id, "customer")
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copyfile(src, os.path.join(dst_dir, fname))
        copied.append((kb_id, fname))
    return copied
