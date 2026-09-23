"""MemoryDecayService — 每日定时 importance 衰减 + 归档（STOP C 正式接线）。

契约（2026-09-24 STOP C 冻结）：
  - 互斥区间：>180 天未访问 ×0.9；90~180 天 ×0.95（旧实现两档级联，
    >180 天被双重衰减，接线时修正）
  - importance < 0.2 → 归档（is_active=False）
  - **explicit 完全豁免**：用户显式记忆不衰减、不自动归档——
    「以后一直用中文」不因几个月未访问而消失/降权
  - 只操作 is_active 行，永不触碰 superseded 历史版本链
  - 已知限制：主 L3 注入路径暂不 mark_accessed（STOP D ownership），
    recency 信号偏保守 → 参数从宽，不做激进收缩
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
        # < 0.2 → archive（explicit 豁免）
        n_archived = await self._repo.archive_stale(0.2)

        total = n_180 + n_90 + n_archived
        if total:
            logger.info(f"[Decay] 衰减 {n_180 + n_90} 条, 归档 {n_archived} 条")
        return {"decayed": n_180 + n_90, "archived": n_archived}
