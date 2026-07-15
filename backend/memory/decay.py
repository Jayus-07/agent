"""MemoryDecayService — daily scheduled importance decay + archival"""
from backend.utils.logger import logger


class MemoryDecayService:
    def __init__(self, repo):
        self._repo = repo

    async def run(self) -> dict:
        """Execute one decay cycle. Returns stats."""
        # >180 days → importance × 0.9
        n_180 = await self._repo.apply_decay(180, 0.9)
        # >90 days → importance × 0.95
        n_90 = await self._repo.apply_decay(90, 0.95)
        # < 0.2 → archive
        n_archived = await self._repo.archive_stale(0.2)

        total = n_180 + n_90 + n_archived
        if total:
            logger.info(f"[Decay] 衰减 {n_180 + n_90} 条, 归档 {n_archived} 条")
        return {"decayed": n_180 + n_90, "archived": n_archived}
