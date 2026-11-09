"""MemoryDecayService — 每日定时 importance 衰减（ADR-0012 第 2 期 D4 对齐）。

契约（2026-09-24 STOP C 冻结，2026-11-10 第 2 期 D-1 裁决修订）：
  - 互斥区间：>180 天未访问 ×0.9；90~180 天 ×0.95（旧实现两档级联，
    >180 天被双重衰减，接线时修正）
  - **explicit 完全豁免**：用户显式记忆不衰减——
    「以后一直用中文」不因几个月未访问而消失/降权
  - 只操作 is_active 行，永不触碰 superseded 历史版本链
  - 已知限制：主 L3 注入路径暂不 mark_accessed（STOP D ownership），
    recency 信号偏保守 → 参数从宽，不做激进收缩
  - **D4 边界（2026-11-10 修订，取代 STOP C「<0.2 归档」条款）**：衰减
    只降低自动注入的排序权重（rank 0.3 importance 项），**不删除、不
    隐藏**——原 archive_stale（importance<0.2 → is_active=False）会令
    设置页不可见，违反 D4 边界，已移除。记忆量有界改由 dreaming 整合
    （合并/矛盾失效）+ 用户删除承担；失效统一走 invalid_at 双写通道。
"""
from backend.shared.logger import logger


class MemoryDecayService:
    def __init__(self, repo):
        self._repo = repo

    async def run(self) -> dict:
        """Execute one decay cycle. Returns stats."""
        # >180 days → importance × 0.9
        n_180 = await self._repo.apply_decay(180, 0.9)
        # 90~180 days → importance × 0.95（互斥区间）
        n_90 = await self._repo.apply_decay(90, 0.95, upper_days=180)

        if n_180 + n_90:
            logger.info(f"[Decay] 衰减 {n_180 + n_90} 条（仅降权，不失效不隐藏）")
        return {"decayed": n_180 + n_90}
