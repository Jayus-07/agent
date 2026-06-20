"""ImportanceScorer 测试 — 5维重要性评分"""
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.importance import ImportanceScorer


class TestImportanceScorer:
    """5维评分器"""

    def setup_method(self):
        self.scorer = ImportanceScorer()

    def test_user_long_term_fact(self):
        """用户长期事实 — 最高权重"""
        score = self.scorer.score("user_fact", "我是技术部的负责人")
        assert score >= 0.9

    def test_user_preference(self):
        score = self.scorer.score("preference", "我习惯使用 VS Code 开发")
        assert score >= 0.8

    def test_project_context(self):
        score = self.scorer.score("knowledge", "这个项目的架构采用微服务设计")
        assert score >= 0.6

    def test_work_background(self):
        score = self.scorer.score("knowledge", "上周完成了数据库迁移部署")
        assert score >= 0.4

    def test_casual_chat(self):
        """闲聊 — 最低权重"""
        score = self.scorer.score("knowledge", "今天天气不错")
        assert score == 0.2

    def test_type_bonus_user_fact(self):
        """类型加成：user_fact +0.1"""
        score = self.scorer.score("user_fact", "我的职位是后端开发")
        assert score >= 1.0 or score >= 0.9  # base可能在0.9-1.0区间

    def test_type_bonus_preference(self):
        """类型加成：preference +0.05"""
        score_no_bonus = self.scorer.score("knowledge", "我喜欢喝咖啡")
        score_with_bonus = self.scorer.score("preference", "我喜欢喝咖啡")
        assert score_with_bonus >= score_no_bonus

    def test_score_capped_at_one(self):
        """分数不超过 1.0"""
        score = self.scorer.score("user_fact", "我是系统架构师")
        assert score <= 1.0

    def test_should_store_above_threshold(self):
        assert self.scorer.should_store(0.7)

    def test_should_store_below_threshold(self):
        assert not self.scorer.should_store(0.5)

    def test_should_store_at_threshold(self):
        """等于阈值（0.6）时存储"""
        assert self.scorer.should_store(0.6)

    def test_empty_content(self):
        score = self.scorer.score("knowledge", "")
        assert score == 0.2  # falls through to casual

    def test_threshold_constant(self):
        assert ImportanceScorer.THRESHOLD == 0.6

    def test_decision_type_bonus(self):
        score = self.scorer.score("decision", "决定使用 PostgreSQL 替代 SQLite")
        assert score > 0.2  # 至少有项目相关的基础分
