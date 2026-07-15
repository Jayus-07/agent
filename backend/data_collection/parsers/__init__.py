"""数据解析层 — 将 RawData 转换为结构化记录列表"""
from backend.data_collection.parsers.base import AbstractParser, ParsedData
from backend.data_collection.parsers.json_parser import JsonParser
from backend.data_collection.parsers.csv_parser import CsvParser

__all__ = ["AbstractParser", "ParsedData", "JsonParser", "CsvParser"]
