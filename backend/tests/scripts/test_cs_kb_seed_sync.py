"""cs_kb_seed 种子契约测试：git 源同步逻辑 + 清单与实际文件一致性守卫。

B1（cs-capability-gap）：cs_policy 8 篇政策 + cs_faq 速查首次入种子。
契约测试保证：删文档不改清单（或反之）会在 CI 直接失败，种子不会静默缺失。
"""
from __future__ import annotations

import os
from pathlib import Path

from scripts.cs_kb_seed import (
    SEED_FILES,
    repo_root_source_dir,
    sync_kb_seed_files,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_repo_source_dir_joins_expected_subpath():
    assert repo_root_source_dir(os.path.join("/", "app")) == os.path.join(
        "/", "app", "docs", "customer-service", "kb",
    )


def test_seed_files_cover_policy_base_and_quickref():
    by_kb: dict[str, set[str]] = {}
    for kb_id, fname in SEED_FILES:
        by_kb.setdefault(kb_id, set()).add(fname)

    # cs_policy 首次有种子：8 篇政策文档
    assert by_kb.get("cs_policy") == {
        "refund-policy.md",
        "price-match-policy.md",
        "coupon-policy.md",
        "points-policy.md",
        "payment-faq-policy.md",
        "shipping-policy.md",
        "invoice-policy.md",
        "aftersales-details.md",
    }
    # cs_faq 含高频速查
    assert "faq-quick-reference.md" in by_kb.get("cs_faq", set())


def test_seed_files_all_exist_in_repo():
    """清单里的每一项都必须在 git 源目录真实存在（删文档必须同步删清单）。"""
    source_root = repo_root_source_dir(str(_REPO_ROOT))
    missing = [
        (kb_id, fname)
        for kb_id, fname in SEED_FILES
        if not os.path.isfile(os.path.join(source_root, kb_id, fname))
    ]
    assert missing == [], f"种子清单指向不存在的源文档: {missing}"


def test_sync_copies_git_sources_into_customer_layout(tmp_path):
    source_root = tmp_path / "kb"
    docs_dir = tmp_path / "data" / "docs"
    (source_root / "cs_policy").mkdir(parents=True)
    (source_root / "cs_policy" / "refund-policy.md").write_text(
        "# 退款政策", encoding="utf-8",
    )
    (source_root / "cs_faq").mkdir()
    (source_root / "cs_faq" / "faq-quick-reference.md").write_text(
        "# 速查", encoding="utf-8",
    )

    copied = sync_kb_seed_files(
        str(source_root), str(docs_dir),
        [("cs_policy", "refund-policy.md"), ("cs_faq", "faq-quick-reference.md")],
    )

    assert copied == [("cs_policy", "refund-policy.md"), ("cs_faq", "faq-quick-reference.md")]
    assert (docs_dir / "cs_policy" / "customer" / "refund-policy.md").read_text(
        encoding="utf-8",
    ) == "# 退款政策"
    assert (docs_dir / "cs_faq" / "customer" / "faq-quick-reference.md").read_text(
        encoding="utf-8",
    ) == "# 速查"


def test_sync_skips_missing_source_and_keeps_volume_only_files(tmp_path):
    """源缺失（历史 docker cp 文档）时跳过拷贝，不创建目标目录。"""
    source_root = tmp_path / "kb"
    docs_dir = tmp_path / "data" / "docs"

    copied = sync_kb_seed_files(
        str(source_root), str(docs_dir), [("cs_policy", "refund-policy.md")],
    )

    assert copied == []
    assert not (docs_dir / "cs_policy").exists()


def test_sync_overwrites_changed_source(tmp_path):
    source_root = tmp_path / "kb"
    docs_dir = tmp_path / "data" / "docs"
    policy_dir = source_root / "cs_policy"
    policy_dir.mkdir(parents=True)
    target = docs_dir / "cs_policy" / "customer" / "refund-policy.md"

    (policy_dir / "refund-policy.md").write_text("v1", encoding="utf-8")
    sync_kb_seed_files(str(source_root), str(docs_dir), [("cs_policy", "refund-policy.md")])
    assert target.read_text(encoding="utf-8") == "v1"

    (policy_dir / "refund-policy.md").write_text("v2", encoding="utf-8")
    sync_kb_seed_files(str(source_root), str(docs_dir), [("cs_policy", "refund-policy.md")])
    assert target.read_text(encoding="utf-8") == "v2"
