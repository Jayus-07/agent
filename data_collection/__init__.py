"""
Data Collection Center — 企业级数据采集与治理平台

Pipeline: Fetcher → Parser → Cleaner → Analyzer → Writer

对外 API:
    from data_collection import CollectionPipeline, Scheduler
    from data_collection.tool import data_collection_tool
    from data_collection.skill import DataCollectionSkill
"""

from data_collection.pipeline import CollectionPipeline, CollectResult
from data_collection.scheduler import Scheduler

__all__ = [
    "CollectionPipeline",
    "CollectResult",
    "Scheduler",
]
