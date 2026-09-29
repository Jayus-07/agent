# 智能选品与竞品分析实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于现有竞品快照 pipeline 构建选品引擎（规则打分 + LLM 增强 + RAG 语义趋势），提供 `/selection/*` API 与前端选品页。

**Architecture:** 新增 `backend/selection/` 模块（store/scoring/market_index/trends/recommender 五个单元），复用 `backend/competitor` 快照数据、BGE embedding 单例、llm_polisher 事实锁定模式；前端新建 `/selection` 页并扩展 `/competitors` 页。

**Tech Stack:** Python 3.10 + FastAPI + SQLite + langchain_chroma + langchain LLM 接口；Next.js 14 + Recharts + TypeScript。

**Spec:** `docs/superpowers/specs/2026-08-23-smart-selection-design.md`

**测试命令约定：** 项目 `pytest.ini` 带 `--cov-fail-under=55`，单文件迭代时用 `python -m pytest <file> -v --no-cov`；最后统一跑全量套件验证覆盖率。所有命令在项目根目录（`d:\Program Files\workplace\agent`）执行，PowerShell 用 `;` 而非 `&&`。

---

## 文件结构

**新建：**

| 文件 | 职责 |
|---|---|
| `backend/selection/__init__.py` | 模块导出 |
| `backend/selection/store.py` | SelectionStore（SQLite `data/selection.db`）：评分缓存、权重配置 |
| `backend/selection/scoring.py` | 纯函数加权评分（五维度，无 I/O） |
| `backend/selection/market_index.py` | 快照 → Chroma `competitor_market` collection；语义趋势检索 |
| `backend/selection/trends.py` | 快照 SQL 聚合（价格分位时序、评价增速、卖点词频） |
| `backend/selection/recommender.py` | 编排：候选 → 打分 → LLM 理由（事实锁定）→ 组装 |
| `backend/app/api/routes/selection.py` | `/selection/*` REST 路由 |
| `backend/scripts/backfill_market_index.py` | 历史快照一次性回填市场索引 |
| `backend/tests/test_selection_store.py` | store 单测 |
| `backend/tests/test_selection_scoring.py` | scoring 边界单测 |
| `backend/tests/test_market_index.py` | 索引单测（mock Chroma） |
| `backend/tests/test_selection_trends.py` | trends 单测（临时 SQLite） |
| `backend/tests/test_selection_reason.py` | LLM 理由事实锁定单测 |
| `backend/tests/api/test_selection_routes.py` | 路由契约测试（TestClient） |
| `frontend/src/services/selection.ts` | 前端 service 层 |
| `frontend/src/app/selection/page.tsx` | 选品页（推荐列表 + 趋势区） |
| `frontend/src/components/selection/CompareModal.tsx` | 多品对比表格弹窗 |

**修改：**

| 文件 | 改动 |
|---|---|
| `backend/competitor/pipeline.py` | `analyze_url` 快照入库后挂 `market_index` 索引钩子 |
| `backend/app/api/router.py` | 注册 `selection.router` |
| `backend/app/api/routes/competitor.py` | 新增 `GET /competitor/recommendations` 别名端点 |
| `frontend/src/components/Sidebar.tsx` | 新增"智能选品"导航项 |
| `frontend/src/app/competitors/page.tsx` | 监控表格潜力分列 + 多选对比入口 |

---

### Task 1: SelectionStore（评分缓存 + 权重配置）

**Files:**
- Create: `backend/selection/__init__.py`
- Create: `backend/selection/store.py`
- Test: `backend/tests/test_selection_store.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_selection_store.py
"""SelectionStore 单测 — 评分缓存与权重配置"""
import os
import tempfile

import pytest

from backend.selection.store import SelectionStore, DEFAULT_WEIGHTS


@pytest.fixture
def store(tmp_path):
    return SelectionStore(db_path=str(tmp_path / "selection_test.db"))


class TestScoreCache:
    def test_save_and_get_score(self, store):
        store.save_score("https://a.com", {"total": 82.5, "breakdown": {}, "notes": []}, snapshot_id=7)
        row = store.get_score("https://a.com")
        assert row is not None
        assert row["snapshot_id"] == 7
        assert row["score_json"]["total"] == 82.5
        assert row["computed_at"]

    def test_get_missing_score_returns_none(self, store):
        assert store.get_score("https://none.com") is None

    def test_save_score_upsert(self, store):
        store.save_score("https://a.com", {"total": 60.0, "breakdown": {}, "notes": []}, snapshot_id=1)
        store.save_score("https://a.com", {"total": 70.0, "breakdown": {}, "notes": []}, snapshot_id=2)
        row = store.get_score("https://a.com")
        assert row["score_json"]["total"] == 70.0
        assert row["snapshot_id"] == 2

    def test_all_scores(self, store):
        store.save_score("https://a.com", {"total": 60.0, "breakdown": {}, "notes": []}, None)
        store.save_score("https://b.com", {"total": 80.0, "breakdown": {}, "notes": []}, None)
        assert len(store.all_scores()) == 2


class TestWeights:
    def test_default_weights_when_empty(self, store):
        assert store.get_weights() == DEFAULT_WEIGHTS

    def test_set_and_get_weights(self, store):
        store.set_weights({"reputation": 0.5, "heat": 0.5})
        w = store.get_weights()
        assert w["reputation"] == 0.5
        assert w["heat"] == 0.5
        # 未覆盖的 key 保留默认值
        assert w["stability"] == DEFAULT_WEIGHTS["stability"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest backend/tests/test_selection_store.py -v --no-cov`
Expected: FAIL（`ModuleNotFoundError: backend.selection`）

- [ ] **Step 3: 实现 SelectionStore**

```python
# backend/selection/__init__.py
"""selection — 智能选品引擎

scoring:      规则加权评分（纯函数）
market_index: 快照 → Chroma competitor_market collection（语义趋势）
trends:       快照 SQL 聚合（结构趋势）
recommender:  编排层（打分 + LLM 理由 + 组装）
store:        SelectionStore（评分缓存 / 权重配置）
"""
```

```python
# backend/selection/store.py
"""selection/store.py — 选品引擎存储（SQLite）

两表设计:
  - selection_scores   — 评分结果缓存（快照无更新时命中缓存）
  - selection_weights  — 评分权重配置（可调）

镜像 backend/competitor/store.py 的模式（线程安全单例 + threading.Lock）。
"""
import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Optional

from backend.shared.logger import logger

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
SELECTION_DB_PATH = os.getenv(
    "SELECTION_DB_PATH", os.path.join(_PROJECT_ROOT, "data", "selection.db")
)

# 与 spec §5.1 一致的默认权重
DEFAULT_WEIGHTS: dict[str, float] = {
    "reputation": 0.25,
    "heat": 0.25,
    "price": 0.20,
    "differentiation": 0.15,
    "stability": 0.15,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS selection_scores (
    url         TEXT PRIMARY KEY,
    score_json  TEXT NOT NULL,
    snapshot_id INTEGER,
    computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS selection_weights (
    key        TEXT PRIMARY KEY,
    value      REAL NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class SelectionStore:
    """线程安全的选品引擎存储"""

    def __init__(self, db_path: str = SELECTION_DB_PATH):
        self._db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        logger.info(f"[SelectionStore] 初始化: {db_path}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── 评分缓存 ─────────────────────────────────

    def save_score(self, url: str, score_json: dict[str, Any],
                   snapshot_id: Optional[int]) -> None:
        """UPSERT 一条评分结果"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO selection_scores (url, score_json, snapshot_id, computed_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET
                        score_json=excluded.score_json,
                        snapshot_id=excluded.snapshot_id,
                        computed_at=excluded.computed_at""",
                (url, json.dumps(score_json, ensure_ascii=False), snapshot_id, now),
            )

    def get_score(self, url: str) -> Optional[dict[str, Any]]:
        """读取评分缓存（score_json 已反序列化）"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM selection_scores WHERE url = ?", (url,)
            ).fetchone()
        if row is None:
            return None
        return {
            "url": row["url"],
            "score_json": json.loads(row["score_json"]),
            "snapshot_id": row["snapshot_id"],
            "computed_at": row["computed_at"],
        }

    def all_scores(self) -> list[dict[str, Any]]:
        """全部评分缓存"""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM selection_scores").fetchall()
        return [
            {
                "url": r["url"],
                "score_json": json.loads(r["score_json"]),
                "snapshot_id": r["snapshot_id"],
                "computed_at": r["computed_at"],
            }
            for r in rows
        ]

    # ── 权重配置 ─────────────────────────────────

    def get_weights(self) -> dict[str, float]:
        """当前权重（未配置的 key 用默认值补齐）"""
        weights = dict(DEFAULT_WEIGHTS)
        with self._connect() as conn:
            for row in conn.execute("SELECT key, value FROM selection_weights"):
                if row["key"] in weights:
                    weights[row["key"]] = row["value"]
        return weights

    def set_weights(self, weights: dict[str, float]) -> None:
        """更新权重（仅接受已知 key，忽略未知 key）"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            for key, value in weights.items():
                if key not in DEFAULT_WEIGHTS:
                    continue
                conn.execute(
                    """INSERT INTO selection_weights (key, value, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET
                            value=excluded.value, updated_at=excluded.updated_at""",
                    (key, float(value), now),
                )
        logger.info(f"[SelectionStore] 权重更新: {weights}")


_store: Optional[SelectionStore] = None


def get_selection_store() -> SelectionStore:
    """全局单例"""
    global _store
    if _store is None:
        _store = SelectionStore()
    return _store


def reset_selection_store() -> None:
    """重置全局单例（测试隔离）"""
    global _store
    _store = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest backend/tests/test_selection_store.py -v --no-cov`
Expected: PASS（7 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/selection/__init__.py backend/selection/store.py backend/tests/test_selection_store.py
git commit -m "feat(selection): SelectionStore 评分缓存与权重配置"
```

---

### Task 2: scoring.py 规则评分引擎

**Files:**
- Create: `backend/selection/scoring.py`
- Test: `backend/tests/test_selection_scoring.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_selection_scoring.py
"""scoring 单测 — 五维度加权评分与边界处理（spec §5.1）"""
import pytest

from backend.selection.scoring import (
    DEFAULT_WEIGHTS,
    score_product,
    split_keywords,
)


def _snap(**kw):
    """构造快照 dict，缺省为数据完整的理想快照"""
    base = {
        "id": 1,
        "url": "https://a.com",
        "platform": "taobao",
        "title": "测试商品",
        "price": 100.0,
        "original_price": 120.0,
        "currency": "CNY",
        "promo_text": "",
        "rating": 4.8,
        "review_count": 1000,
        "in_stock": 1,
        "highlights": "无线,降噪,长续航",
        "crawled_at": "2026-08-20T10:00:00",
    }
    base.update(kw)
    return base


class TestReputation:
    def test_high_rating_scores_high(self):
        result = score_product(_snap(rating=4.8), [_snap(rating=4.8)], [_snap(rating=4.8)])
        assert result["breakdown"]["reputation"] == pytest.approx(100.0)

    def test_missing_rating_neutral_with_note(self):
        result = score_product(_snap(rating=None), [_snap(rating=None)], [_snap(rating=None)])
        assert result["breakdown"]["reputation"] == 50.0
        assert "data_insufficient" in result["notes"]


class TestPrice:
    def test_single_item_pool_neutral_quantile(self):
        snap = _snap()
        result = score_product(snap, [snap], [snap])
        assert "single_item_pool" in result["notes"]
        # 分位中性 50 + 折扣分 > 0 → price 在 (25, 75) 区间
        assert 25.0 < result["breakdown"]["price"] < 75.0

    def test_cheapest_in_pool_scores_higher(self):
        cheap = _snap(url="https://cheap.com", price=50.0, original_price=None)
        expensive = _snap(url="https://exp.com", price=200.0, original_price=None)
        r_cheap = score_product(cheap, [cheap], [cheap, expensive])
        r_exp = score_product(expensive, [expensive], [cheap, expensive])
        assert r_cheap["breakdown"]["price"] > r_exp["breakdown"]["price"]

    def test_missing_price_neutral(self):
        result = score_product(_snap(price=None), [_snap(price=None)], [_snap(price=None)])
        assert result["breakdown"]["price"] == 50.0


class TestHeat:
    def test_missing_review_count_neutral(self):
        result = score_product(_snap(review_count=None), [_snap(review_count=None)],
                               [_snap(review_count=None)])
        assert result["breakdown"]["heat"] == 50.0

    def test_review_growth_boosts_heat(self):
        old = _snap(id=1, review_count=100, crawled_at="2026-08-10T10:00:00")
        new = _snap(id=2, review_count=1000, crawled_at="2026-08-20T10:00:00")
        stagnant = _snap(id=3, url="https://b.com", review_count=1000,
                         crawled_at="2026-08-20T10:00:00")
        r_growing = score_product(new, [new, old], [new, stagnant])
        r_flat = score_product(stagnant, [stagnant], [new, stagnant])
        assert r_growing["breakdown"]["heat"] > r_flat["breakdown"]["heat"]


class TestDifferentiation:
    def test_unique_highlights_score_high(self):
        me = _snap(highlights="独家,专利,新品")
        other = _snap(url="https://b.com", highlights="无线,降噪,长续航")
        result = score_product(me, [me], [me, other])
        assert result["breakdown"]["differentiation"] == pytest.approx(100.0)

    def test_identical_highlights_score_zero(self):
        me = _snap(highlights="无线,降噪")
        other = _snap(url="https://b.com", highlights="无线,降噪")
        result = score_product(me, [me], [me, other])
        assert result["breakdown"]["differentiation"] == pytest.approx(0.0)

    def test_empty_pool_neutral(self):
        snap = _snap()
        result = score_product(snap, [snap], [snap])
        assert result["breakdown"]["differentiation"] == 50.0


class TestStability:
    def test_insufficient_history_neutral(self):
        snap = _snap()
        result = score_product(snap, [snap], [snap])
        assert result["breakdown"]["stability"] == 50.0
        assert "insufficient_history" in result["notes"]

    def test_stable_price_high_stock_scores_high(self):
        snaps = [_snap(id=i, price=100.0 + i * 0.1, crawled_at=f"2026-08-{10 + i}T10:00:00")
                 for i in range(5)]
        latest = snaps[-1]
        result = score_product(latest, list(reversed(snaps)), [latest])
        assert result["breakdown"]["stability"] > 90.0


class TestAggregation:
    def test_total_in_range_and_breakdown_complete(self):
        snap = _snap()
        result = score_product(snap, [snap], [snap])
        assert 0.0 <= result["total"] <= 100.0
        assert set(result["breakdown"]) == {
            "reputation", "heat", "price", "differentiation", "stability"
        }

    def test_weights_normalized_when_not_summing_to_one(self):
        snap = _snap(rating=4.8)
        heavy = {"reputation": 1.0, "heat": 1.0, "price": 0.0,
                 "differentiation": 0.0, "stability": 0.0}
        result = score_product(snap, [snap], [snap], weights=heavy)
        # 权重归一化后 reputation/heat 各占 0.5
        expected = 0.5 * result["breakdown"]["reputation"] + 0.5 * result["breakdown"]["heat"]
        assert result["total"] == pytest.approx(expected)

    def test_notes_deduplicated(self):
        snap = _snap(rating=None, review_count=None, price=None)
        result = score_product(snap, [snap], [snap])
        assert result["notes"].count("data_insufficient") == 1


class TestSplitKeywords:
    def test_split_comma_and_chinese_comma(self):
        assert split_keywords("无线,降噪，长续航") == {"无线", "降噪", "长续航"}

    def test_empty_string(self):
        assert split_keywords("") == set()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest backend/tests/test_selection_scoring.py -v --no-cov`
Expected: FAIL（`ImportError: cannot import name 'score_product'`）

- [ ] **Step 3: 实现 scoring.py**

```python
# backend/selection/scoring.py
"""selection/scoring.py — 产品潜力规则评分（spec §5.1）

纯函数、无 I/O：输入快照 dict，输出 {total, breakdown, notes}。
五维度各归一化到 0-100，加权求和；边界场景走中性分 50 并记录 notes。

维度:
  reputation      口碑分   rating 线性映射
  heat            热度分   评价量级 + 评价增速
  price           价格竞争力 折扣力度 + 池内分位反向
  differentiation 卖点差异度 卖点关键词 Jaccard 重合率反向
  stability       稳定性   价格变异系数反向 + 有货率
"""
import bisect
import math
import statistics
from datetime import datetime
from typing import Any, Optional

DEFAULT_WEIGHTS: dict[str, float] = {
    "reputation": 0.25,
    "heat": 0.25,
    "price": 0.20,
    "differentiation": 0.15,
    "stability": 0.15,
}

_NEUTRAL = 50.0


def split_keywords(highlights: str) -> set[str]:
    """卖点字符串 → 关键词集合（兼容中英文逗号/分号）"""
    if not highlights:
        return set()
    parts = highlights.replace("，", ",").replace("；", ",").replace(";", ",").split(",")
    return {p.strip() for p in parts if p.strip()}


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ── 各维度评分（返回 (分数, note 或 None)）──────────────

def _reputation(latest: dict) -> tuple[float, Optional[str]]:
    rating = latest.get("rating")
    if rating is None:
        return _NEUTRAL, "data_insufficient"
    return _clip01((rating - 4.0) / 0.8) * 100, None


def _review_growth_per_day(history: list[dict]) -> Optional[float]:
    """最近两条含评价数的快照间的日增速（history 新→旧）"""
    pts = [
        (s.get("crawled_at"), s.get("review_count"))
        for s in history
        if s.get("review_count") is not None and s.get("crawled_at")
    ]
    if len(pts) < 2:
        return None
    try:
        t_new = datetime.fromisoformat(pts[0][0])
        t_old = datetime.fromisoformat(pts[1][0])
    except ValueError:
        return None
    days = (t_new - t_old).total_seconds() / 86400
    if days <= 0:
        return None
    return (pts[0][1] - pts[1][1]) / days


def _heat(latest: dict, history: list[dict],
          pool_latest: list[dict]) -> tuple[float, Optional[str]]:
    rc = latest.get("review_count")
    if rc is None:
        return _NEUTRAL, "data_insufficient"
    rcs = [s["review_count"] for s in pool_latest if s.get("review_count") is not None]
    max_rc = max(rcs) if rcs else rc
    if max_rc > 0:
        magnitude = math.log10(rc + 1) / math.log10(max_rc + 1) * 100
    else:
        magnitude = _NEUTRAL
    growth = _review_growth_per_day(history)
    if growth is None:
        growth_score = _NEUTRAL
    else:
        # 饱和归一：日增 20 条 ≈ 50 分，日增 180 条 ≈ 90 分
        growth_score = 100 * max(growth, 0.0) / (max(growth, 0.0) + 20.0)
    return 0.7 * magnitude + 0.3 * growth_score, None


def _price(latest: dict, pool_latest: list[dict]) -> tuple[float, Optional[str]]:
    price = latest.get("price")
    if price is None:
        return _NEUTRAL, "data_insufficient"
    # 折扣力度：40% 折扣即满分
    orig = latest.get("original_price")
    if orig and orig > price:
        discount = _clip01((orig - price) / orig * 2.5) * 100
    else:
        discount = _NEUTRAL
    # 池内价格分位反向（越便宜分越高）
    prices = sorted(s["price"] for s in pool_latest if s.get("price") is not None)
    if len(prices) < 2:
        return 0.5 * discount + 0.5 * _NEUTRAL, "single_item_pool"
    rank = bisect.bisect_left(prices, price)
    quantile_rev = (1 - rank / (len(prices) - 1)) * 100
    return 0.5 * discount + 0.5 * quantile_rev, None


def _differentiation(latest: dict, pool_latest: list[dict]) -> tuple[float, Optional[str]]:
    kws = split_keywords(latest.get("highlights") or "")
    others = [
        split_keywords(s.get("highlights") or "")
        for s in pool_latest
        if s.get("url") != latest.get("url")
    ]
    others = [o for o in others if o]
    if not others:
        return _NEUTRAL, "single_item_pool"
    if not kws:
        return _NEUTRAL, "data_insufficient"
    overlaps = [len(kws & o) / len(kws | o) for o in others]
    return (1 - sum(overlaps) / len(overlaps)) * 100, None


def _stability(history: list[dict]) -> tuple[float, Optional[str]]:
    if len(history) < 2:
        return _NEUTRAL, "insufficient_history"
    priced = [s["price"] for s in history if s.get("price") is not None]
    if len(priced) < 2:
        return _NEUTRAL, "insufficient_history"
    mean = statistics.mean(priced)
    cv = statistics.pstdev(priced) / mean if mean else 0.0
    cv_score = max(0.0, 1 - cv * 5) * 100  # 变异系数 ≥20% → 0 分
    stock_rate = sum(1 for s in history if s.get("in_stock")) / len(history) * 100
    return 0.5 * cv_score + 0.5 * stock_rate, None


# ── 主入口 ──────────────────────────────────────

def score_product(
    latest: dict[str, Any],
    history: list[dict[str, Any]],
    pool_latest: list[dict[str, Any]],
    weights: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """对单个商品计算潜力分。

    参数:
        latest:      该商品最新快照
        history:     该商品历史快照（新→旧，来自 CompetitorStore.history）
        pool_latest: 候选池内全部商品的最新快照（含自身）
        weights:     权重（None = 默认；和 ≠ 1 时自动归一化）

    返回: {"total": float, "breakdown": {dim: score}, "notes": [str]}
    """
    w = dict(weights or DEFAULT_WEIGHTS)
    total_w = sum(w.values())
    if total_w <= 0:
        w = dict(DEFAULT_WEIGHTS)
        total_w = 1.0

    dims = {
        "reputation": _reputation(latest),
        "heat": _heat(latest, history, pool_latest),
        "price": _price(latest, pool_latest),
        "differentiation": _differentiation(latest, pool_latest),
        "stability": _stability(history),
    }

    breakdown = {k: round(v[0], 1) for k, v in dims.items()}
    notes: list[str] = []
    for _, note in dims.values():
        if note and note not in notes:
            notes.append(note)

    total = sum(w[k] / total_w * v[0] for k, v in dims.items())
    return {"total": round(total, 1), "breakdown": breakdown, "notes": notes}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest backend/tests/test_selection_scoring.py -v --no-cov`
Expected: PASS（14 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/selection/scoring.py backend/tests/test_selection_scoring.py
git commit -m "feat(selection): 五维度加权潜力评分引擎"
```

---

### Task 3: market_index.py 市场索引 + pipeline 挂钩子

**Files:**
- Create: `backend/selection/market_index.py`
- Modify: `backend/competitor/pipeline.py`（`analyze_url` 第 3 步快照入库后）
- Test: `backend/tests/test_market_index.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_market_index.py
"""market_index 单测 — mock Chroma，验证文档格式与检索封装"""
from unittest.mock import MagicMock, patch

import pytest

from backend.selection.market_index import MarketIndex, build_doc


def _snap():
    return {
        "id": 42,
        "url": "https://item.taobao.com/item.htm?id=1",
        "platform": "taobao",
        "title": "无线降噪耳机",
        "price": 129.0,
        "original_price": 199.0,
        "currency": "CNY",
        "promo_text": "限时立减",
        "rating": 4.8,
        "review_count": 12000,
        "in_stock": 1,
        "highlights": "无线,降噪,长续航",
        "crawled_at": "2026-08-23T08:00:00",
    }


class TestBuildDoc:
    def test_doc_text_and_id(self):
        doc_id, text, meta = build_doc(_snap())
        assert doc_id == "snap-42"
        assert "无线降噪耳机" in text
        assert "卖点:无线,降噪,长续航" in text
        assert "促销:限时立减" in text

    def test_metadata_fields(self):
        _, _, meta = build_doc(_snap())
        assert meta["url"] == "https://item.taobao.com/item.htm?id=1"
        assert meta["platform"] == "taobao"
        assert meta["snapshot_id"] == 42
        assert meta["price_band"] in ("low", "mid", "high")

    def test_missing_price_band_empty(self):
        snap = _snap()
        snap["price"] = None
        _, _, meta = build_doc(snap)
        assert meta["price_band"] == ""


class TestMarketIndex:
    def _make_index(self):
        idx = MarketIndex.__new__(MarketIndex)
        idx._chroma = MagicMock()
        return idx

    def test_index_snapshot_calls_upsert(self):
        idx = self._make_index()
        doc_id = idx.index_snapshot(_snap())
        assert doc_id == "snap-42"
        idx._chroma._collection.upsert.assert_called_once()

    def test_index_snapshot_without_id_returns_empty(self):
        idx = self._make_index()
        snap = _snap()
        snap["id"] = None
        assert idx.index_snapshot(snap) == ""
        idx._chroma._collection.upsert.assert_not_called()

    def test_search_trends_applies_filter(self):
        idx = self._make_index()
        doc = MagicMock()
        doc.page_content = "正文"
        doc.metadata = {"url": "u"}
        idx._chroma.similarity_search_with_score.return_value = [(doc, 0.2)]
        hits = idx.search_trends("耳机", k=5, metadata_filter={"platform": "taobao"})
        assert len(hits) == 1
        assert hits[0]["text"] == "正文"
        idx._chroma.similarity_search_with_score.assert_called_once_with(
            "耳机", k=5, filter={"platform": "taobao"}
        )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest backend/tests/test_market_index.py -v --no-cov`
Expected: FAIL（`ModuleNotFoundError` 或 `ImportError`）

- [ ] **Step 3: 实现 market_index.py**

```python
# backend/selection/market_index.py
"""selection/market_index.py — 竞品市场语义索引（spec §3.2 / §4.2）

独立 Chroma collection `competitor_market`（persist: data/chroma_market），
不共用主知识库 persist 目录（ChromaKnowledgeStore 未指定 collection_name）。
embedding 复用 backend.rag.embedding_singleton 全局 BGE 实例，保证向量空间一致。

每条快照 → 一条文档（id = snap-{snapshot_id}，保留时序）。
"""
from typing import Any, Optional

from backend.shared.logger import logger

_COLLECTION = "competitor_market"

import os
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
MARKET_PERSIST_DIR = os.getenv(
    "MARKET_PERSIST_DIR", os.path.join(_PROJECT_ROOT, "data", "chroma_market")
)


def build_doc(snap: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """快照 → (doc_id, text, metadata)"""
    price = snap.get("price")
    band = ""
    if price is not None:
        band = "low" if price < 100 else ("mid" if price < 500 else "high")
    text = (
        f"{snap.get('title') or ''}｜平台:{snap.get('platform') or 'generic'}"
        f"｜价格:{price}{snap.get('currency') or 'CNY'}"
        f"｜卖点:{snap.get('highlights') or ''}"
        f"｜促销:{snap.get('promo_text') or ''}"
    )
    meta = {
        "url": snap.get("url") or "",
        "platform": snap.get("platform") or "generic",
        "category": snap.get("category") or "",
        "price_band": band,
        "crawled_at": snap.get("crawled_at") or "",
        "snapshot_id": snap.get("id") or 0,
    }
    return f"snap-{snap.get('id')}", text, meta


class MarketIndex:
    """竞品市场语义索引（懒加载 embedding，构造不触发 400MB 模型加载）"""

    def __init__(self, persist_directory: str = MARKET_PERSIST_DIR):
        self._persist_directory = persist_directory
        self._chroma = None

    def _ensure(self):
        if self._chroma is None:
            from langchain_chroma import Chroma
            from backend.rag.embedding_singleton import get_embedding
            os.makedirs(self._persist_directory, exist_ok=True)
            self._chroma = Chroma(
                collection_name=_COLLECTION,
                persist_directory=self._persist_directory,
                embedding_function=get_embedding(),
            )
            logger.info(f"[MarketIndex] 就绪: {self._persist_directory}")
        return self._chroma

    def index_snapshot(self, snap: dict[str, Any]) -> str:
        """索引一条快照，返回 doc id（无 id 时跳过返回空串）"""
        if not snap.get("id"):
            return ""
        doc_id, text, meta = build_doc(snap)
        self._ensure()._collection.upsert(ids=[doc_id], documents=[text], metadatas=[meta])
        return doc_id

    def search_trends(self, query: str, k: int = 10,
                      metadata_filter: Optional[dict] = None) -> list[dict[str, Any]]:
        """语义趋势检索（独立于主 RAG 管线）"""
        docs = self._ensure().similarity_search_with_score(query, k=k, filter=metadata_filter)
        return [
            {"text": d.page_content, "metadata": d.metadata, "score": float(s)}
            for d, s in docs
        ]

    def count(self) -> int:
        """collection 文档总数"""
        return self._ensure()._collection.count()


_index: Optional[MarketIndex] = None


def get_market_index() -> MarketIndex:
    """全局单例"""
    global _index
    if _index is None:
        _index = MarketIndex()
    return _index


def reset_market_index() -> None:
    """重置全局单例（测试隔离）"""
    global _index
    _index = None


def index_snapshot_safe(snap: dict[str, Any]) -> None:
    """供 competitor pipeline 调用的安全钩子：失败仅记日志，不影响采集主流程"""
    try:
        get_market_index().index_snapshot(snap)
    except Exception as e:
        logger.warning(f"[MarketIndex] 快照索引失败（忽略）: {e}")
```

- [ ] **Step 4: 修改 competitor/pipeline.py 挂钩子**

在 `analyze_url` 中，`snap_id = store.save_snapshot({...})` 成功分支之后（`else:` 块结束处、第 4 步"与上次快照对比"之前）插入：

```python
    # 3b. 市场语义索引（失败不影响主流程）
    if snap_id is not None:
        from backend.selection.market_index import index_snapshot_safe
        saved = store.latest_snapshot(url)
        if saved:
            index_snapshot_safe(saved)
```

> 说明：`save_snapshot` 只返回 id，从 store 读回完整快照行以获得 `crawled_at` / `title` 等入库字段。

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest backend/tests/test_market_index.py backend/tests/test_selection_scoring.py -v --no-cov`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add backend/selection/market_index.py backend/competitor/pipeline.py backend/tests/test_market_index.py
git commit -m "feat(selection): 市场语义索引 competitor_market 与 pipeline 挂钩子"
```

---

### Task 4: trends.py 结构趋势聚合

**Files:**
- Create: `backend/selection/trends.py`
- Test: `backend/tests/test_selection_trends.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_selection_trends.py
"""trends 单测 — 临时 CompetitorStore 灌入快照，验证聚合"""
import pytest

from backend.competitor.store import CompetitorStore
from backend.selection.trends import compute_trends


@pytest.fixture
def store(tmp_path):
    s = CompetitorStore(db_path=str(tmp_path / "competitor_test.db"))
    s.save_snapshot({"url": "https://a.com", "platform": "taobao", "title": "A",
                     "price": 100.0, "review_count": 100, "in_stock": 1,
                     "highlights": "无线,降噪", "crawled_at": "2026-08-01T10:00:00"})
    s.save_snapshot({"url": "https://a.com", "platform": "taobao", "title": "A",
                     "price": 90.0, "review_count": 400, "in_stock": 1,
                     "highlights": "无线,长续航", "crawled_at": "2026-08-11T10:00:00"})
    s.save_snapshot({"url": "https://b.com", "platform": "jd", "title": "B",
                     "price": 200.0, "review_count": 50, "in_stock": 0,
                     "highlights": "降噪", "crawled_at": "2026-08-10T10:00:00"})
    return s


class TestComputeTrends:
    def test_review_growth_per_url(self, store):
        t = compute_trends(store, days=0)
        by_url = {g["url"]: g for g in t["review_growth"]}
        assert by_url["https://a.com"]["daily_delta"] == pytest.approx(30.0)  # 300 / 10 天
        assert "https://b.com" not in by_url  # 单快照无增速

    def test_highlight_freq_sorted_desc(self, store):
        t = compute_trends(store, days=0)
        freq = {h["keyword"]: h["count"] for h in t["highlight_freq"]}
        assert freq["降噪"] == 2
        assert freq["无线"] == 2
        assert freq["长续航"] == 1

    def test_price_quantiles_per_day(self, store):
        t = compute_trends(store, days=0)
        dates = {q["date"] for q in t["price_quantiles"]}
        assert "2026-08-11" in dates
        q = next(x for x in t["price_quantiles"] if x["date"] == "2026-08-11")
        assert q["p50"] <= q["p75"]

    def test_days_filter(self, store):
        t = compute_trends(store, days=15, now_iso="2026-08-12T00:00:00")
        assert t["sources"]["snapshot_count"] == 2  # 仅 8-01 之后的两条

    def test_platform_filter(self, store):
        t = compute_trends(store, days=0, platform="taobao")
        assert t["sources"]["snapshot_count"] == 2

    def test_empty_store(self, tmp_path):
        s = CompetitorStore(db_path=str(tmp_path / "empty.db"))
        t = compute_trends(s, days=0)
        assert t["items"] == []
        assert t["price_quantiles"] == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest backend/tests/test_selection_trends.py -v --no-cov`
Expected: FAIL（`ImportError: cannot import name 'compute_trends'`）

- [ ] **Step 3: 实现 trends.py**

```python
# backend/selection/trends.py
"""selection/trends.py — 结构趋势聚合（spec §4.2）

直接对 competitor_snapshots 聚合，数字不经 LLM：
  - price_quantiles: 按天的价格 p25/p50/p75
  - review_growth:   每个 URL 的评价数日增速
  - highlight_freq:  卖点关键词词频
"""
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

from backend.selection.scoring import split_keywords


def compute_trends(
    store,
    days: int = 30,
    platform: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """从 CompetitorStore 聚合趋势数据。

    参数:
        store:   CompetitorStore 实例
        days:    最近 N 天（0 = 全部）
        platform: 平台过滤（None = 全部）
        now_iso: 测试注入的"当前时间"（ISO 字符串）
    """
    now = datetime.fromisoformat(now_iso) if now_iso else datetime.now()

    # 全量读快照（watchlist 规模下内存聚合足够；量大后迁移 SQL 窗口函数）
    with store._connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM competitor_snapshots ORDER BY crawled_at").fetchall()]

    if days > 0:
        cutoff = (now - timedelta(days=days)).isoformat()
        rows = [r for r in rows if (r.get("crawled_at") or "") >= cutoff]
    if platform:
        rows = [r for r in rows if r.get("platform") == platform]

    # ── 价格分位数（按天）──
    by_day: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get("price") is not None and r.get("crawled_at"):
            by_day[r["crawled_at"][:10]].append(r["price"])
    price_quantiles = []
    for date in sorted(by_day):
        prices = sorted(by_day[date])
        price_quantiles.append({
            "date": date,
            "p25": _quantile(prices, 0.25),
            "p50": _quantile(prices, 0.50),
            "p75": _quantile(prices, 0.75),
        })

    # ── 评价增速（每 URL 最近两条含评价数的快照）──
    by_url: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_url[r["url"]].append(r)
    review_growth = []
    watch_names = {w["url"]: w["name"] for w in store.list_watch(enabled_only=False)}
    for url, snaps in by_url.items():
        pts = [s for s in snaps if s.get("review_count") is not None and s.get("crawled_at")]
        if len(pts) < 2:
            continue
        old, new = pts[-2], pts[-1]
        span_days = (datetime.fromisoformat(new["crawled_at"])
                     - datetime.fromisoformat(old["crawled_at"])).total_seconds() / 86400
        if span_days <= 0:
            continue
        review_growth.append({
            "url": url,
            "name": watch_names.get(url) or new.get("title") or url,
            "daily_delta": round((new["review_count"] - old["review_count"]) / span_days, 1),
        })
    review_growth.sort(key=lambda g: g["daily_delta"], reverse=True)

    # ── 卖点词频（每 URL 取最新非空 highlights，避免历史重复计数）──
    freq: Counter = Counter()
    for url, snaps in by_url.items():
        latest_hl = next((s.get("highlights") for s in reversed(snaps) if s.get("highlights")), "")
        freq.update(split_keywords(latest_hl))
    highlight_freq = [{"keyword": k, "count": c} for k, c in freq.most_common(20)]

    # ── 商品条目（供前端表格）──
    items = []
    for url, snaps in by_url.items():
        latest = snaps[-1]
        items.append({
            "url": url,
            "name": watch_names.get(url) or latest.get("title") or url,
            "platform": latest.get("platform") or "generic",
            "latest_price": latest.get("price"),
            "rating": latest.get("rating"),
            "review_count": latest.get("review_count"),
            "highlights": latest.get("highlights") or "",
            "latest_crawled_at": latest.get("crawled_at"),
        })

    return {
        "days": days,
        "platform": platform,
        "items": items,
        "price_quantiles": price_quantiles,
        "review_growth": review_growth,
        "highlight_freq": highlight_freq,
        "sources": {"snapshot_count": len(rows), "rag_hits": 0},
    }


def _quantile(sorted_vals: list[float], q: float) -> float:
    """简单线性插值分位数（sorted_vals 非空）"""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = q * (len(sorted_vals) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return round(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac, 2)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest backend/tests/test_selection_trends.py -v --no-cov`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/selection/trends.py backend/tests/test_selection_trends.py
git commit -m "feat(selection): 结构趋势聚合（价格分位/评价增速/卖点词频）"
```

---

### Task 5: recommender.py 编排层 + LLM 理由（事实锁定）

**Files:**
- Create: `backend/selection/recommender.py`
- Test: `backend/tests/test_selection_reason.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_selection_reason.py
"""LLM 推荐理由生成单测 — 事实锁定校验（mock LLM）"""
from unittest.mock import MagicMock, patch

from backend.selection.recommender import generate_reason

_PAYLOAD = {
    "title": "无线降噪耳机",
    "platform": "taobao",
    "latest_price": 129.0,
    "currency": "CNY",
    "rating": 4.8,
    "review_count": 12000,
    "highlights": "无线,降噪,长续航",
    "score": {
        "total": 82.5,
        "breakdown": {"reputation": 90, "heat": 75, "price": 80,
                      "differentiation": 70, "stability": 88},
        "notes": [],
    },
}


class TestGenerateReason:
    def test_fallback_when_llm_fails(self):
        with patch("backend.selection.recommender.llm") as mock_llm:
            mock_llm.invoke.side_effect = RuntimeError("llm down")
            result = generate_reason(_PAYLOAD)
        assert result["llm_reason"]
        assert "82.5" in result["llm_reason"]
        assert "LLM" in result["llm_risks"]

    def test_number_tampering_falls_back(self):
        fake = MagicMock()
        fake.content = "该商品潜力分高达 99 分，评价数 999999，强烈推荐。"
        with patch("backend.selection.recommender.llm") as mock_llm:
            mock_llm.invoke.return_value = fake
            result = generate_reason(_PAYLOAD)
        # LLM 篡改数字 → 回退模板，不含伪造数字
        assert "999999" not in result["llm_reason"]
        assert "82.5" in result["llm_reason"]

    def test_valid_llm_output_kept(self):
        fake = MagicMock()
        fake.content = "潜力分 82.5，评价数 12000，评分 4.8，口碑与热度俱佳。"
        with patch("backend.selection.recommender.llm") as mock_llm:
            mock_llm.invoke.return_value = fake
            result = generate_reason(_PAYLOAD)
        assert result["llm_reason"] == fake.content

    def test_notes_mentioned_in_risks(self):
        payload = {**_PAYLOAD, "score": {**_PAYLOAD["score"], "notes": ["data_insufficient"]}}
        with patch("backend.selection.recommender.llm") as mock_llm:
            mock_llm.invoke.side_effect = RuntimeError("llm down")
            result = generate_reason(payload)
        # note 经 _NOTE_LABELS 映射为中文标签
        assert "部分字段缺失" in result["llm_risks"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest backend/tests/test_selection_reason.py -v --no-cov`
Expected: FAIL（`ImportError: cannot import name 'generate_reason'`）

- [ ] **Step 3: 实现 recommender.py**

```python
# backend/selection/recommender.py
"""selection/recommender.py — 选品引擎编排层（spec §2 / §5）

候选（watchlist 快照）→ 规则打分 → LLM 理由（事实锁定）→ 组装推荐结果。
供 REST 路由与后续对话 Skill 共用。
"""
from datetime import datetime
from typing import Any, Optional

from backend.competitor.store import get_store
from backend.infra.llm import llm
from backend.selection.scoring import score_product
from backend.selection.store import get_selection_store
from backend.shared.logger import logger

# 复用 llm_polisher 的数值提取（事实锁定模式，spec §5.2）
from backend.business_report.llm_polisher import _extract_numbers

_NOTE_LABELS = {
    "data_insufficient": "部分字段缺失",
    "single_item_pool": "候选池内缺少同类对比",
    "insufficient_history": "历史快照不足",
}


def _pool() -> tuple[list[dict], list[dict]]:
    """返回 (候选 URL 列表, 全部商品最新快照)。评分池 = 全部启用监控项。"""
    store = get_store()
    items = store.list_watch(enabled_only=True)
    pool_latest = []
    urls = []
    for item in items:
        snap = store.latest_snapshot(item["url"])
        if snap and (snap.get("price") is not None or snap.get("title")):
            pool_latest.append(snap)
            urls.append(item["url"])
    return urls, pool_latest


def _build_item(url: str, pool_latest: list[dict], weights: dict,
                use_llm: bool = True, force_refresh: bool = False) -> Optional[dict]:
    """组装单个商品的推荐条目（打分 + 缓存 + LLM 理由）"""
    store = get_store()
    sel_store = get_selection_store()

    latest = store.latest_snapshot(url)
    if not latest:
        return None

    cached = sel_store.get_score(url)
    if (not force_refresh and cached is not None
            and cached.get("snapshot_id") == latest.get("id")):
        score = cached["score_json"]
        scored_at = cached["computed_at"]
    else:
        history = store.history(url, limit=50)
        score = score_product(latest, history, pool_latest, weights)
        sel_store.save_score(url, score, latest.get("id"))
        scored_at = datetime.now().isoformat(timespec="seconds")

    item = {
        "url": url,
        "title": latest.get("title") or url,
        "platform": latest.get("platform") or "generic",
        "latest_price": latest.get("price"),
        "currency": latest.get("currency") or "CNY",
        "rating": latest.get("rating"),
        "review_count": latest.get("review_count"),
        "score": score,
        "llm_reason": "",
        "llm_risks": "",
        "latest_crawled_at": latest.get("crawled_at"),
        "scored_at": scored_at,
    }
    if use_llm:
        item.update(generate_reason(item))
    return item


def generate_reason(payload: dict[str, Any]) -> dict[str, str]:
    """基于打分与快照字段生成推荐理由/风险提示。

    事实锁定：LLM 输出中的数值必须覆盖输入中的全部数值 token，
    否则回退模板文案（防编造数字）。
    """
    score = payload.get("score") or {}
    fallback_reason = (
        f"潜力分 {score.get('total')}（口碑 {score.get('breakdown', {}).get('reputation')} / "
        f"热度 {score.get('breakdown', {}).get('heat')} / "
        f"价格 {score.get('breakdown', {}).get('price')}），"
        f"现价 {payload.get('latest_price')}{payload.get('currency', 'CNY')}，"
        f"评分 {payload.get('rating')}，评价数 {payload.get('review_count')}。"
    )
    notes = score.get("notes") or []
    fallback_risks = (
        "；".join(_NOTE_LABELS.get(n, n) for n in notes)
        if notes else "暂无明显风险信号。"
    )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        messages = [
            SystemMessage(content=(
                "你是电商选品分析师。根据给定数据写 1-2 句推荐理由。"
                "严格规则：只允许使用给定数据中出现的数字，禁止编造、推算或改写任何数字。"
            )),
            HumanMessage(content=(
                f"商品: {payload.get('title')}\n"
                f"平台: {payload.get('platform')}\n"
                f"现价: {payload.get('latest_price')} {payload.get('currency')}\n"
                f"评分: {payload.get('rating')} 评价数: {payload.get('review_count')}\n"
                f"卖点: {payload.get('highlights', '')}\n"
                f"潜力分: {score.get('total')} 分维度: {score.get('breakdown')}\n"
                f"数据缺口标注: {notes}"
            )),
        ]
        resp = llm.invoke(messages)
        text = resp.content.strip()
    except Exception as e:
        logger.warning(f"[Recommender] LLM 理由生成失败，回退模板: {e}")
        return {"llm_reason": fallback_reason,
                "llm_risks": f"LLM 理由生成失败，以下为规则摘要。{fallback_risks}"}

    # 事实锁定校验：输出中出现的每个数字必须可在输入数据中溯源（禁止编造）。
    # 注意与 llm_polisher 方向相反：润色要求保留全部事实，理由生成只写 1-2 句，
    # 因此校验"输出 ⊆ 输入"而非"输入 ⊆ 输出"。
    source_facts = (
        f"潜力分 {score.get('total')} 现价 {payload.get('latest_price')} "
        f"评分 {payload.get('rating')} 评价数 {payload.get('review_count')} "
        f"分维度 {score.get('breakdown')}"
    )
    allowed = _extract_numbers(source_facts)
    present = _extract_numbers(text)
    fabricated = {t for t in present if t not in allowed
                  and t.replace(",", "") not in allowed}
    if fabricated:
        logger.warning(f"[Recommender] LLM 输出含编造数字 {fabricated}，回退模板")
        return {"llm_reason": fallback_reason, "llm_risks": fallback_risks}
    return {"llm_reason": text, "llm_risks": fallback_risks}


def recommend(limit: int = 10, platform: Optional[str] = None,
              min_score: float = 0.0, use_llm: bool = True) -> dict[str, Any]:
    """推荐列表（潜力分降序）"""
    sel_store = get_selection_store()
    urls, pool_latest = _pool()
    weights = sel_store.get_weights()
    items = []
    for url in urls:
        snap = next((s for s in pool_latest if s.get("url") == url), None)
        if platform and (snap or {}).get("platform") != platform:
            continue
        item = _build_item(url, pool_latest, weights, use_llm=use_llm)
        if item and item["score"]["total"] >= min_score:
            items.append(item)
    items.sort(key=lambda x: x["score"]["total"], reverse=True)
    items = items[:limit]
    return {
        "items": items,
        "total": len(items),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def score_url(url: str, force_refresh: bool = False) -> Optional[dict[str, Any]]:
    """单品潜力评估（不带 LLM 理由，供前端潜力分列/单品页）"""
    _, pool_latest = _pool()
    weights = get_selection_store().get_weights()
    return _build_item(url, pool_latest, weights,
                       use_llm=False, force_refresh=force_refresh)


def batch_scores(urls: list[str]) -> dict[str, Any]:
    """批量读评分缓存（不触发重算；供监控表潜力分列）"""
    sel_store = get_selection_store()
    scores = {}
    for url in urls:
        cached = sel_store.get_score(url)
        if cached:
            scores[url] = cached["score_json"]
    return {"scores": scores,
            "generated_at": datetime.now().isoformat(timespec="seconds")}


def compare(urls: list[str]) -> dict[str, Any]:
    """多品对比：最新快照并排 + 差异字段列表"""
    store = get_store()
    items = []
    for url in urls:
        snap = store.latest_snapshot(url)
        watch = store.get_watch_by_url(url)
        items.append({
            "url": url,
            "name": (watch["name"] if watch else "") or (snap or {}).get("title") or url,
            "price": (snap or {}).get("price"),
            "original_price": (snap or {}).get("original_price"),
            "currency": (snap or {}).get("currency") or "CNY",
            "rating": (snap or {}).get("rating"),
            "review_count": (snap or {}).get("review_count"),
            "promo_text": (snap or {}).get("promo_text") or "",
            "in_stock": bool((snap or {}).get("in_stock")),
            "highlights": (snap or {}).get("highlights") or "",
            "crawled_at": (snap or {}).get("crawled_at"),
        })
    diff_fields = []
    for field in ("price", "rating", "review_count", "promo_text", "in_stock", "highlights"):
        if len({str(it.get(field)) for it in items}) > 1:
            diff_fields.append(field)
    return {"items": items, "diff_fields": diff_fields,
            "generated_at": datetime.now().isoformat(timespec="seconds")}


def generate_report(category: str = "", days: int = 30) -> str:
    """选品 Markdown 报告（同步返回，与 /competitor/scan 行为一致）"""
    from backend.selection.trends import compute_trends

    rec = recommend(limit=10, use_llm=False)
    trends = compute_trends(get_store(), days=days)
    lines = [
        f"## 智能选品报告（{datetime.now().strftime('%Y-%m-%d %H:%M')}）",
        "",
        f"数据窗口: 最近 {days} 天，快照 {trends['sources']['snapshot_count']} 条",
        "",
        "### Top 推荐",
        "",
        "| 商品 | 平台 | 现价 | 评分 | 评价数 | 潜力分 |",
        "|---|---|---|---|---|---|",
    ]
    for it in rec["items"]:
        lines.append(
            f"| {it['title'][:30]} | {it['platform']} | "
            f"{it['latest_price'] if it['latest_price'] is not None else '-'} | "
            f"{it['rating'] if it['rating'] is not None else '-'} | "
            f"{it['review_count'] if it['review_count'] is not None else '-'} | "
            f"{it['score']['total']} |"
        )
    if trends["highlight_freq"]:
        top_kw = "、".join(h["keyword"] for h in trends["highlight_freq"][:8])
        lines.extend(["", "### 热卖卖点 Top8", "", top_kw])
    if trends["review_growth"]:
        lines.extend(["", "### 评价增速 Top3", ""])
        for g in trends["review_growth"][:3]:
            lines.append(f"- {g['name']}: 日增 {g['daily_delta']} 条")
    return "\n".join(lines)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest backend/tests/test_selection_reason.py -v --no-cov`
Expected: PASS（4 passed）

> 若 `_extract_numbers` 在 `llm_polisher.py` 中不可导入（名称有出入），先用 Grep 确认实际函数名再调整 import。

- [ ] **Step 5: 提交**

```bash
git add backend/selection/recommender.py backend/tests/test_selection_reason.py
git commit -m "feat(selection): 推荐编排层与事实锁定 LLM 理由生成"
```

---

### Task 6: REST 路由（/selection/* + /competitor/recommendations 别名）

**Files:**
- Create: `backend/app/api/routes/selection.py`
- Modify: `backend/app/api/router.py`（imports + include_router 各一行）
- Modify: `backend/app/api/routes/competitor.py`（新增别名端点）
- Test: `backend/tests/api/test_selection_routes.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/api/test_selection_routes.py
"""selection 路由契约测试 — 最小 FastAPI app + TestClient + mock recommender"""
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes.selection import router

_REC_PAYLOAD = {
    "items": [{
        "url": "https://a.com", "title": "A", "platform": "taobao",
        "latest_price": 99.0, "currency": "CNY", "rating": 4.8,
        "review_count": 100,
        "score": {"total": 80.0, "breakdown": {}, "notes": []},
        "llm_reason": "理由", "llm_risks": "",
        "latest_crawled_at": "2026-08-23T08:00:00",
        "scored_at": "2026-08-23T10:00:00",
    }],
    "total": 1, "generated_at": "2026-08-23T10:00:00",
}


def _client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestRecommendations:
    def test_returns_items(self):
        with patch("backend.app.api.routes.selection.recommend", return_value=_REC_PAYLOAD):
            resp = _client().get("/selection/recommendations")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["score"]["total"] == 80.0
        assert "generated_at" in body

    def test_invalid_limit_rejected(self):
        resp = _client().get("/selection/recommendations?limit=0")
        assert resp.status_code == 422


class TestScore:
    def test_score_found(self):
        with patch("backend.app.api.routes.selection.score_url",
                   return_value={"url": "https://a.com", "score": {"total": 75.0}}):
            resp = _client().post("/selection/score", json={"url": "https://a.com"})
        assert resp.status_code == 200
        assert resp.json()["score"]["total"] == 75.0

    def test_score_404_when_no_snapshot(self):
        with patch("backend.app.api.routes.selection.score_url", return_value=None):
            resp = _client().post("/selection/score", json={"url": "https://none.com"})
        assert resp.status_code == 404


class TestOthers:
    def test_batch_scores(self):
        with patch("backend.app.api.routes.selection.batch_scores",
                   return_value={"scores": {}, "generated_at": "t"}):
            resp = _client().get("/selection/scores/batch?urls=https%3A%2F%2Fa.com")
        assert resp.status_code == 200

    def test_compare_requires_two_urls(self):
        resp = _client().get("/selection/compare?urls=https%3A%2F%2Fa.com")
        assert resp.status_code == 422

    def test_weights_put_rejects_unknown_key(self):
        resp = _client().put("/selection/weights", json={"weights": {"foo": 0.5}})
        assert resp.status_code == 422

    def test_weights_get(self):
        resp = _client().get("/selection/weights")
        assert resp.status_code == 200
        assert "reputation" in resp.json()["weights"]
```

> 说明：路由内用函数内延迟 import（见 Step 3），因此 patch 目标是路由模块内引用的名称。若实现时改为模块顶层 import，需同步把 patch 目标改为 `backend.selection.recommender.*`。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest backend/tests/api/test_selection_routes.py -v --no-cov`
Expected: FAIL（`ModuleNotFoundError: backend.app.api.routes.selection`）

- [ ] **Step 3: 实现 selection.py 路由**

```python
# backend/app/api/routes/selection.py
"""selection REST API — 智能选品结构化端点（spec §7）

路由前缀: /selection（经 next.config.js rewrite 由 /api/selection 代理）。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.selection.recommender import (
    batch_scores,
    compare as do_compare,
    generate_report,
    recommend,
    score_url,
)
from backend.selection.store import DEFAULT_WEIGHTS, get_selection_store
from backend.shared.logger import logger

router = APIRouter(prefix="/selection", tags=["智能选品"])


class ScoreRequest(BaseModel):
    url: str = Field(..., min_length=1, description="商品 URL")
    force_refresh: bool = Field(False, description="强制重算（忽略缓存）")


class WeightsRequest(BaseModel):
    weights: dict[str, float] = Field(..., description="权重字典，key 必须属于五维度")


@router.get("/recommendations")
def recommendations(
    category: str = Query("", description="品类过滤（Phase 2 生效）"),
    platform: Optional[str] = Query(None, description="平台过滤"),
    limit: int = Query(10, ge=1, le=50, description="返回条数"),
    min_score: float = Query(0.0, ge=0.0, le=100.0, description="潜力分下限"),
):
    """推荐列表（潜力分降序 + LLM 理由）"""
    return recommend(limit=limit, platform=platform, min_score=min_score)


@router.get("/trends")
def trends(days: int = Query(30, ge=0, le=365),
           platform: Optional[str] = Query(None)):
    """趋势聚合数据（结构趋势 + 语义检索计数）"""
    from backend.competitor.store import get_store
    from backend.selection.trends import compute_trends
    result = compute_trends(get_store(), days=days, platform=platform)
    # 语义趋势检索作为可选增强，失败不阻塞结构化数据返回
    try:
        from backend.selection.market_index import get_market_index
        hits = get_market_index().search_trends(
            "市场趋势 热卖卖点 促销", k=10,
            metadata_filter={"platform": platform} if platform else None)
        result["sources"]["rag_hits"] = len(hits)
    except Exception as e:
        logger.warning(f"[selection:api] 语义趋势检索失败（忽略）: {e}")
    return result


@router.post("/score")
def score(req: ScoreRequest):
    """单品潜力评估"""
    item = score_url(req.url, force_refresh=req.force_refresh)
    if item is None:
        raise HTTPException(status_code=404, detail=f"无快照数据: {req.url}")
    return item


@router.get("/scores/batch")
def scores_batch(urls: list[str] = Query(..., description="商品 URL 列表")):
    """批量读评分缓存（供监控表潜力分列）"""
    return batch_scores(urls)


@router.get("/compare")
def compare(urls: list[str] = Query(..., min_length=2, description="至少两个 URL")):
    """多品对比数据"""
    return do_compare(urls)


@router.get("/weights")
def get_weights():
    """读取评分权重"""
    return {"weights": get_selection_store().get_weights(), "default": DEFAULT_WEIGHTS}


@router.put("/weights")
def put_weights(req: WeightsRequest):
    """更新评分权重（仅接受五维度 key）"""
    unknown = set(req.weights) - set(DEFAULT_WEIGHTS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"未知权重 key: {sorted(unknown)}")
    get_selection_store().set_weights(req.weights)
    return {"weights": get_selection_store().get_weights()}


@router.post("/report")
def report(category: str = "", days: int = Query(30, ge=1, le=365)):
    """选品报告（同步返回 markdown）"""
    return {"report": generate_report(category=category, days=days)}
```

- [ ] **Step 4: 注册路由 + 别名端点**

`backend/app/api/router.py`：imports 元组加入 `selection`，业务路由区加一行：

```python
api_router.include_router(selection.router)  # 智能选品
```

`backend/app/api/routes/competitor.py` 末尾追加别名端点：

```python
# ── GET /competitor/recommendations ─────────────

@router.get("/recommendations")
def competitor_recommendations(
    limit: int = Query(10, ge=1, le=50),
    platform: str = Query(None, description="平台过滤"),
    min_score: float = Query(0.0, ge=0.0, le=100.0),
):
    """推荐列表别名端点（直接调选品引擎，与 /selection/recommendations 等价）"""
    from backend.selection.recommender import recommend
    return recommend(limit=limit, platform=platform, min_score=min_score)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest backend/tests/api/test_selection_routes.py -v --no-cov`
Expected: PASS（8 passed）

- [ ] **Step 6: 提交**

```bash
git add backend/app/api/routes/selection.py backend/app/api/router.py backend/app/api/routes/competitor.py backend/tests/api/test_selection_routes.py
git commit -m "feat(selection): /selection REST 路由与 /competitor/recommendations 别名"
```

---

### Task 7: 历史快照回填脚本

**Files:**
- Create: `backend/scripts/backfill_market_index.py`

- [ ] **Step 1: 实现脚本**

```python
# backend/scripts/backfill_market_index.py
"""一次性回填：将 competitor_snapshots 全量历史索引到 competitor_market collection。

用法（项目根目录）:
    python -m backend.scripts.backfill_market_index [--dry-run]
"""
import argparse

from backend.competitor.store import get_store
from backend.selection.market_index import get_market_index


def main() -> None:
    parser = argparse.ArgumentParser(description="回填市场语义索引")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = parser.parse_args()

    store = get_store()
    with store._connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM competitor_snapshots ORDER BY id").fetchall()]

    index = get_market_index()
    written = 0
    for row in rows:
        if args.dry_run:
            written += 1
            continue
        if index.index_snapshot(row):
            written += 1
    mode = "dry-run" if args.dry_run else "写入"
    print(f"[backfill] {mode} {written}/{len(rows)} 条快照 → competitor_market")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 冒烟验证**

Run: `python -m backend.scripts.backfill_market_index --dry-run`
Expected: 输出 `[backfill] dry-run N/N 条快照 → competitor_market`（N 为现有快照数，无异常栈）

- [ ] **Step 3: 提交**

```bash
git add backend/scripts/backfill_market_index.py
git commit -m "feat(selection): 市场索引历史回填脚本"
```

---

### Task 8: 前端 service 层 selection.ts

**Files:**
- Create: `frontend/src/services/selection.ts`

- [ ] **Step 1: 实现 service**

```typescript
// frontend/src/services/selection.ts
/**
 * 智能选品 service
 *
 * 后端为 backend/app/api/routes/selection.py（前缀 /selection，
 * 经 next.config.js rewrite 由 /api/selection 代理）。
 * 必须使用相对路径 + request()，禁止 NEXT_PUBLIC_API_URL（会绕过代理）。
 */

import { request } from '@/lib/fetcher'

const BASE = '/selection'

// ── 类型定义 ──────────────────────────────────

/** 评分结果（与后端 score_product 输出一致） */
export interface ScoreResult {
  total: number
  breakdown: {
    reputation: number
    heat: number
    price: number
    differentiation: number
    stability: number
  }
  notes: string[]
}

/** 推荐列表项 */
export interface RecommendationItem {
  url: string
  title: string
  platform: string
  latest_price: number | null
  currency: string
  rating: number | null
  review_count: number | null
  score: ScoreResult
  llm_reason: string
  llm_risks: string
  latest_crawled_at: string | null
  scored_at: string
}

/** 趋势聚合响应 */
export interface TrendsData {
  days: number
  platform: string | null
  items: {
    url: string
    name: string
    platform: string
    latest_price: number | null
    rating: number | null
    review_count: number | null
    highlights: string
    latest_crawled_at: string | null
  }[]
  price_quantiles: { date: string; p25: number; p50: number; p75: number }[]
  review_growth: { url: string; name: string; daily_delta: number }[]
  highlight_freq: { keyword: string; count: number }[]
  sources: { snapshot_count: number; rag_hits: number }
}

/** 对比项 */
export interface CompareItem {
  url: string
  name: string
  price: number | null
  original_price: number | null
  currency: string
  rating: number | null
  review_count: number | null
  promo_text: string
  in_stock: boolean
  highlights: string
  crawled_at: string | null
}

// ── Service ──────────────────────────────────

export const selectionService = {
  /** 推荐列表 */
  getRecommendations: (params?: { platform?: string; limit?: number; min_score?: number }) => {
    const qs = new URLSearchParams()
    if (params?.platform) qs.set('platform', params.platform)
    if (params?.limit) qs.set('limit', String(params.limit))
    if (params?.min_score) qs.set('min_score', String(params.min_score))
    const suffix = qs.toString() ? `?${qs}` : ''
    return request<{ items: RecommendationItem[]; total: number; generated_at: string }>(
      `${BASE}/recommendations${suffix}`,
      { timeout: 120_000 }, // LLM 理由生成可能较慢
    )
  },

  /** 趋势聚合 */
  getTrends: (days = 30, platform?: string) => {
    const qs = new URLSearchParams({ days: String(days) })
    if (platform) qs.set('platform', platform)
    return request<TrendsData>(`${BASE}/trends?${qs}`)
  },

  /** 单品评分 */
  score: (url: string, forceRefresh = false) =>
    request<RecommendationItem>(
      `${BASE}/score`,
      { method: 'POST', body: JSON.stringify({ url, force_refresh: forceRefresh }), timeout: 60_000 },
    ),

  /** 批量评分缓存 */
  batchScores: (urls: string[]) => {
    const qs = urls.map((u) => `urls=${encodeURIComponent(u)}`).join('&')
    return request<{ scores: Record<string, ScoreResult>; generated_at: string }>(
      `${BASE}/scores/batch?${qs}`,
    )
  },

  /** 多品对比 */
  compare: (urls: string[]) => {
    const qs = urls.map((u) => `urls=${encodeURIComponent(u)}`).join('&')
    return request<{ items: CompareItem[]; diff_fields: string[]; generated_at: string }>(
      `${BASE}/compare?${qs}`,
    )
  },

  /** 读取权重 */
  getWeights: () =>
    request<{ weights: Record<string, number>; default: Record<string, number> }>(
      `${BASE}/weights`,
    ),

  /** 更新权重 */
  putWeights: (weights: Record<string, number>) =>
    request<{ weights: Record<string, number> }>(
      `${BASE}/weights`,
      { method: 'PUT', body: JSON.stringify({ weights }) },
    ),

  /** 选品报告 */
  generateReport: (days = 30) =>
    request<{ report: string }>(
      `${BASE}/report?days=${days}`,
      { method: 'POST', timeout: 120_000 },
    ),
}
```

- [ ] **Step 2: 类型检查**

Run（frontend 目录）: `npx tsc --noEmit`
Expected: 无与 `selection.ts` 相关的错误（既有错误不处理）

- [ ] **Step 3: 提交**

```bash
git add frontend/src/services/selection.ts
git commit -m "feat(selection): 前端 selection service 层"
```

---

### Task 9: 前端 /selection 选品页

**Files:**
- Create: `frontend/src/app/selection/page.tsx`

- [ ] **Step 1: 实现页面**

```tsx
// frontend/src/app/selection/page.tsx
'use client'

/**
 * /selection — 智能选品页面
 *
 * 功能模块：
 *   - 推荐列表：潜力分徽章、子分数、LLM 推荐理由折叠、加入监控
 *   - 品类趋势区：价格分位面积图、卖点词频条形图、评价增速列表
 */

import { useCallback, useEffect, useState } from 'react'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend,
} from 'recharts'
import { Sparkles, RefreshCw, Plus, ChevronDown, ChevronUp } from 'lucide-react'
import {
  selectionService, RecommendationItem, TrendsData,
} from '@/services/selection'
import { competitorService } from '@/services/competitor'

function scoreColor(total: number): string {
  if (total >= 80) return 'bg-emerald-100 text-emerald-700'
  if (total >= 60) return 'bg-blue-100 text-blue-700'
  return 'bg-gray-100 text-gray-600'
}

export default function SelectionPage() {
  const [items, setItems] = useState<RecommendationItem[]>([])
  const [trends, setTrends] = useState<TrendsData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [watchedMsg, setWatchedMsg] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [rec, tr] = await Promise.all([
        selectionService.getRecommendations({ limit: 10 }),
        selectionService.getTrends(30),
      ])
      setItems(rec.items)
      setTrends(tr)
    } catch (e) {
      setError(`加载失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const addToWatch = async (item: RecommendationItem) => {
    try {
      await competitorService.addWatch({ url: item.url, name: item.title, platform: item.platform })
      setWatchedMsg((m) => ({ ...m, [item.url]: '已加入监控' }))
    } catch (e) {
      setWatchedMsg((m) => ({ ...m, [item.url]: `加入失败: ${e instanceof Error ? e.message : e}` }))
    }
  }

  return (
    <div className="p-6 space-y-6">
      {/* 标题栏 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <Sparkles size={20} /> 智能选品
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            基于竞品快照的规则评分 + LLM 推荐理由；数据新鲜度以抓取时间为准
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1 px-3 py-1.5 text-sm border rounded-md hover:bg-gray-50 disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 刷新
        </button>
      </div>

      {error && <div className="text-sm text-red-600 bg-red-50 p-3 rounded">{error}</div>}

      {/* 推荐列表 */}
      <section className="border rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b bg-gray-50 text-sm font-medium">潜力推荐 Top {items.length}</div>
        {items.length === 0 && !loading ? (
          <div className="p-8 text-center text-sm text-gray-400">
            暂无推荐数据：请先在竞品监控页添加监控项并抓取快照
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b">
                <th className="px-4 py-2">商品</th>
                <th className="px-4 py-2">平台</th>
                <th className="px-4 py-2 text-right">现价</th>
                <th className="px-4 py-2 text-right">评分</th>
                <th className="px-4 py-2 text-right">评价数</th>
                <th className="px-4 py-2 text-center">潜力分</th>
                <th className="px-4 py-2">数据新鲜度</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <>
                  <tr key={it.url} className="border-b hover:bg-gray-50">
                    <td className="px-4 py-2 max-w-[240px] truncate" title={it.title}>{it.title}</td>
                    <td className="px-4 py-2">{it.platform}</td>
                    <td className="px-4 py-2 text-right">
                      {it.latest_price != null ? `${it.latest_price.toFixed(2)}` : '-'}
                    </td>
                    <td className="px-4 py-2 text-right">{it.rating ?? '-'}</td>
                    <td className="px-4 py-2 text-right">{it.review_count?.toLocaleString() ?? '-'}</td>
                    <td className="px-4 py-2 text-center">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${scoreColor(it.score.total)}`}>
                        {it.score.total}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-400">
                      {it.latest_crawled_at?.slice(0, 16) ?? '-'}
                    </td>
                    <td className="px-4 py-2 text-right whitespace-nowrap">
                      <button
                        onClick={() => setExpanded((s) => ({ ...s, [it.url]: !s[it.url] }))}
                        className="text-gray-400 hover:text-gray-600 mr-2"
                        title="推荐理由"
                      >
                        {expanded[it.url] ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                      </button>
                      <button
                        onClick={() => addToWatch(it)}
                        className="text-blue-600 hover:text-blue-700 inline-flex items-center gap-0.5 text-xs"
                      >
                        <Plus size={14} /> 监控
                      </button>
                      {watchedMsg[it.url] && <span className="ml-1 text-xs text-gray-400">{watchedMsg[it.url]}</span>}
                    </td>
                  </tr>
                  {expanded[it.url] && (
                    <tr key={`${it.url}-detail`} className="border-b bg-gray-50/50">
                      <td colSpan={8} className="px-4 py-3 text-xs space-y-1">
                        <div className="text-gray-600">
                          子分数：口碑 {it.score.breakdown.reputation} / 热度 {it.score.breakdown.heat} / 价格 {it.score.breakdown.price} / 差异 {it.score.breakdown.differentiation} / 稳定 {it.score.breakdown.stability}
                          {it.score.notes.length > 0 && (
                            <span className="ml-2 text-amber-600">⚠ {it.score.notes.join(', ')}</span>
                          )}
                        </div>
                        {it.llm_reason && <div className="text-gray-700">推荐理由：{it.llm_reason}</div>}
                        {it.llm_risks && <div className="text-amber-700">风险提示：{it.llm_risks}</div>}
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* 品类趋势区 */}
      {trends && (
        <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="border rounded-lg p-4">
            <div className="text-sm font-medium mb-3">价格分位趋势（p25 / p50 / p75）</div>
            {trends.price_quantiles.length === 0 ? (
              <div className="text-xs text-gray-400 py-8 text-center">暂无价格数据</div>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={trends.price_quantiles}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Legend />
                  <Area type="monotone" dataKey="p25" stroke="#94a3b8" fill="#e2e8f0" />
                  <Area type="monotone" dataKey="p50" stroke="#3b82f6" fill="#bfdbfe" />
                  <Area type="monotone" dataKey="p75" stroke="#6366f1" fill="#c7d2fe" />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="border rounded-lg p-4">
            <div className="text-sm font-medium mb-3">热卖卖点词频</div>
            {trends.highlight_freq.length === 0 ? (
              <div className="text-xs text-gray-400 py-8 text-center">暂无卖点数据</div>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={trends.highlight_freq.slice(0, 10)} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis type="number" tick={{ fontSize: 11 }} />
                  <YAxis type="category" dataKey="keyword" width={80} tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Bar dataKey="count" fill="#3b82f6" />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="border rounded-lg p-4 lg:col-span-2">
            <div className="text-sm font-medium mb-3">评价增速 Top5（条/天）</div>
            {trends.review_growth.length === 0 ? (
              <div className="text-xs text-gray-400 py-4 text-center">需要 ≥2 次快照才能计算增速</div>
            ) : (
              <ul className="text-sm divide-y">
                {trends.review_growth.slice(0, 5).map((g) => (
                  <li key={g.url} className="py-2 flex justify-between">
                    <span className="truncate max-w-[70%]">{g.name}</span>
                    <span className="text-emerald-600 font-medium">+{g.daily_delta} / 天</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}
    </div>
  )
}
```

- [ ] **Step 2: 类型检查**

Run（frontend 目录）: `npx tsc --noEmit`
Expected: 无与 `selection/page.tsx` 相关的错误

> 注意：tbody 内用了 Fragment 子元素，若 lint 报 key 警告，把 `<>...</>` 改为 `import { Fragment } from 'react'` 并写 `<Fragment key={it.url}>`。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/app/selection/page.tsx
git commit -m "feat(selection): 前端选品页（推荐列表 + 趋势图表）"
```

---

### Task 10: 扩展 /competitors 页（潜力分列 + CompareModal）

**Files:**
- Create: `frontend/src/components/selection/CompareModal.tsx`
- Modify: `frontend/src/app/competitors/page.tsx`

- [ ] **Step 1: 实现 CompareModal**

```tsx
// frontend/src/components/selection/CompareModal.tsx
'use client'

/**
 * 多品对比弹窗：价格/评分/评价数/促销/库存/卖点并排，差异单元格高亮。
 * 数据来源 GET /selection/compare。
 */

import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { selectionService, CompareItem } from '@/services/selection'

interface Props {
  urls: string[]
  onClose: () => void
}

const FIELDS: { key: keyof CompareItem; label: string }[] = [
  { key: 'price', label: '现价' },
  { key: 'original_price', label: '划线价' },
  { key: 'rating', label: '评分' },
  { key: 'review_count', label: '评价数' },
  { key: 'promo_text', label: '促销' },
  { key: 'in_stock', label: '库存' },
  { key: 'highlights', label: '卖点' },
]

function render(val: unknown): string {
  if (val === null || val === undefined || val === '') return '-'
  if (typeof val === 'boolean') return val ? '有货' : '无货'
  return String(val)
}

export default function CompareModal({ urls, onClose }: Props) {
  const [items, setItems] = useState<CompareItem[]>([])
  const [diffFields, setDiffFields] = useState<string[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    selectionService.compare(urls)
      .then((r) => { setItems(r.items); setDiffFields(r.diff_fields) })
      .catch((e) => setError(String(e)))
  }, [urls])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-lg shadow-xl max-w-4xl w-full mx-4 max-h-[80vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b sticky top-0 bg-white">
          <div className="text-sm font-medium">竞品对比（{items.length} 项）</div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X size={18} /></button>
        </div>
        {error && <div className="p-4 text-sm text-red-600">{error}</div>}
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left">
              <th className="px-4 py-2 text-gray-500 w-24">字段</th>
              {items.map((it) => (
                <th key={it.url} className="px-4 py-2 max-w-[200px] truncate" title={it.name}>
                  {it.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {FIELDS.map(({ key, label }) => {
              const isDiff = diffFields.includes(key)
              return (
                <tr key={key} className="border-b">
                  <td className="px-4 py-2 text-gray-500">
                    {label}{isDiff && <span className="ml-1 text-amber-500">•</span>}
                  </td>
                  {items.map((it) => (
                    <td key={it.url} className={`px-4 py-2 ${isDiff ? 'bg-amber-50' : ''}`}>
                      {render(it[key])}
                    </td>
                  ))}
                </tr>
              )
            })}
            <tr>
              <td className="px-4 py-2 text-gray-500">抓取时间</td>
              {items.map((it) => (
                <td key={it.url} className="px-4 py-2 text-xs text-gray-400">
                  {it.crawled_at?.slice(0, 16) ?? '-'}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 修改 competitors/page.tsx — 三处集成**

在 `frontend/src/app/competitors/page.tsx` 中：

a) 顶部 import 区新增：

```tsx
import CompareModal from '@/components/selection/CompareModal'
import { selectionService, ScoreResult } from '@/services/selection'
```

b) 监控列表组件的 state 区（与现有 `watchlist` 等 useState 并列）新增：

```tsx
const [selectedUrls, setSelectedUrls] = useState<string[]>([])
const [compareOpen, setCompareOpen] = useState(false)
const [scores, setScores] = useState<Record<string, ScoreResult>>({})
```

并在加载监控列表成功后（`getWatchlist` 的 then/await 之后）补一次批量评分读取：

```tsx
// 潜力分批量读取（失败不影响列表展示）
if (items.length > 0) {
  selectionService.batchScores(items.map((i) => i.url))
    .then((r) => setScores(r.scores))
    .catch(() => {})
}
```

（其中 `items` 为监控列表数据变量名，按实际代码上下文对齐。）

c) 监控表格新增两列：

- 表头首列：`<th className="px-2 py-2 w-8"><input type="checkbox" ... /></th>`（全选逻辑可省，仅行级勾选即可）
- 每行首列：勾选框，`onChange` 维护 `selectedUrls`（上限 5 项，超出时 alert 提示）
- "潜力分"列（放在"最新价格"列之后）：

```tsx
<td className="px-4 py-2 text-center">
  {scores[item.url]
    ? <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-blue-100 text-blue-700">{scores[item.url].total}</span>
    : <span className="text-gray-300">-</span>}
</td>
```

d) 表格上方工具栏区域加对比按钮，页面底部挂载弹窗：

```tsx
<button
  onClick={() => setCompareOpen(true)}
  disabled={selectedUrls.length < 2}
  className="px-3 py-1.5 text-sm border rounded-md disabled:opacity-40 hover:bg-gray-50"
>
  对比（{selectedUrls.length}）
</button>

{compareOpen && (
  <CompareModal urls={selectedUrls} onClose={() => setCompareOpen(false)} />
)}
```

- [ ] **Step 3: 类型检查 + 手工验证**

Run（frontend 目录）: `npx tsc --noEmit`
Expected: 无相关错误；`npm run dev` 后打开 /competitors，勾选 2+ 项可弹出对比表格，差异字段高亮

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/selection/CompareModal.tsx frontend/src/app/competitors/page.tsx
git commit -m "feat(selection): 竞品页潜力分列与多品对比弹窗"
```

---

### Task 11: 侧边导航入口

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: 新增导航项**

在 `NAV` 数组中"竞品监控"项之后插入（`Sparkles` 图标已在 Sidebar 顶部 import 中存在，无需新增 import）：

```tsx
  {
    icon: <Sparkles size={18} />, label: '智能选品', path: '/selection',
  },
```

- [ ] **Step 2: 手工验证**

启动前端后侧边栏出现"智能选品"，点击跳转 /selection。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/Sidebar.tsx
git commit -m "feat(selection): 侧边导航新增智能选品入口"
```

---

### Task 12: 全量测试 + E2E 验证

**Files:** 无新增

- [ ] **Step 1: 后端全量测试（含覆盖率门禁）**

Run: `python -m pytest backend/tests -v`
Expected: 全部 PASS，覆盖率 ≥55%（pytest.ini 门禁）。若新增测试拉低总体覆盖率，检查是否漏了 `--no-cov` 残留参数；新增模块本身应有完整单测覆盖。

- [ ] **Step 2: 回填历史快照到市场索引**

Run: `python -m backend.scripts.backfill_market_index`
Expected: `[backfill] 写入 N/N 条快照 → competitor_market`

- [ ] **Step 3: API 冒烟（后端已启动时）**

Run: `Invoke-RestMethod http://localhost:8000/selection/weights`
Expected: 返回五维默认权重

Run: `Invoke-RestMethod http://localhost:8000/selection/recommendations?limit=5`
Expected: `{items, total, generated_at}`；有监控数据时 items 非空且按潜力分降序

Run: `Invoke-RestMethod http://localhost:8000/competitor/recommendations?limit=3`
Expected: 与上一端点同构（别名生效）

> PowerShell 中勿用 curl -s（是 Invoke-WebRequest 别名），统一用 Invoke-RestMethod。

- [ ] **Step 4: 浏览器 E2E**

验证清单：
1. /selection 页推荐列表渲染，潜力分徽章颜色分档正确，理由可展开
2. "监控"按钮点击后提示已加入，/competitors 页可见新项
3. 趋势区价格分位面积图、卖点词频条形图渲染（无数据时显示空态文案）
4. /competitors 页勾选 2 项 → 对比弹窗，差异字段高亮（琥珀色）
5. 侧边栏"智能选品"导航高亮与跳转

- [ ] **Step 5: 提交（如有 E2E 修复）**

```bash
git add -A
git commit -m "test(selection): E2E 验证与修复"
```

---

## 自检结论（writing-plans self-review）

**1. Spec 覆盖**：§3 数据模型 → Task 1；§4 趋势 → Task 3/4；§5 评分 → Task 2/5；§6 前端 → Task 8-11；§7 API → Task 6；§9 Phase 1 步骤 1-7 → Task 1-12 一一对应。Phase 2 条目（discover/candidates/榜单采集）不在本计划（属下一份计划）。
**2. 占位符扫描**：无 TBD/TODO；所有代码步骤均含完整代码。
**3. 类型一致性**：`score_product(latest, history, pool_latest, weights)` 在 Task 2 定义、Task 5 调用签名一致；`compute_trends(store, days, platform, now_iso)` 在 Task 4 定义、Task 5/6 调用一致；前端 `ScoreResult.breakdown` 五维度 key 与后端 breakdown 一致；`recommend/score_url/batch_scores/compare/generate_report` 在 Task 5 定义、Task 6 顶层 import 使用。
