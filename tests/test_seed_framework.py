"""种子数据框架核心测试 — Context, Profile, Validator, Exporter。"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from seed_data.core.context import GenerationContext
from seed_data.core.profile import SeedProfile, EntitySpec
from seed_data.core.validator import ValidationResult, SeedValidator
from seed_data.validators.referential import ReferentialValidator
from seed_data.validators.volume import VolumeValidator
from seed_data.exporters.json_file import JsonFileExporter
from seed_data.exporters.python_dict import DictExporter


# ======================= Fixtures =======================

@pytest.fixture
def tiny_profile():
    return SeedProfile.from_name("tiny")


@pytest.fixture
def ctx(tiny_profile):
    return GenerationContext(tiny_profile, seed=42)


# ======================= TestSeedProfile =======================

class TestSeedProfile:
    def test_load_tiny_profile(self):
        profile = SeedProfile.from_name("tiny")
        assert profile.name == "tiny"
        assert profile.entity_count("brand") == 3
        assert profile.entity_count("sku") == 30
        assert profile.entity_count("order") == 500

    def test_load_mvp_profile(self):
        profile = SeedProfile.from_name("mvp")
        assert profile.name == "mvp"
        assert profile.entity_count("sku") == 1000
        assert profile.entity_count("order") == 50000

    def test_entity_spec_max_depth(self):
        profile = SeedProfile.from_name("tiny")
        spec = profile.entity_spec("category")
        assert spec.max_depth == 2

    def test_missing_entity_raises_keyerror(self):
        profile = SeedProfile.from_name("tiny")
        with pytest.raises(KeyError):
            profile.entity_count("nonexistent")

    def test_has_entity(self):
        profile = SeedProfile.from_name("tiny")
        assert profile.has_entity("brand") is True
        assert profile.has_entity("nonexistent") is False

    def test_get_distribution(self):
        profile = SeedProfile.from_name("mvp")
        statuses = profile.get_distribution("order", "status")
        assert statuses is not None
        assert "DELIVERED" in statuses
        assert statuses["DELIVERED"] == 0.70

    def test_get_distribution_default(self):
        profile = SeedProfile.from_name("tiny")
        val = profile.get_distribution("nonexistent", "key", default={})
        assert val == {}

    def test_nonexistent_profile_file(self):
        with pytest.raises(FileNotFoundError):
            SeedProfile.from_name("nonexistent_profile_xyz")


# ======================= TestGenerationContext =======================

class TestGenerationContext:
    def test_register_and_retrieve(self, ctx):
        ctx.register("brand", {"id": "B001", "name": "TestBrand"})
        entities = ctx.get_entities("brand")
        assert len(entities) == 1
        assert entities[0]["name"] == "TestBrand"

    def test_register_batch(self, ctx):
        entities = [{"id": f"B{i:03d}", "name": f"Brand{i}"} for i in range(5)]
        ctx.register_batch("brand", entities)
        assert ctx.count("brand") == 5

    def test_get_entity_by_key(self, ctx):
        ctx.register("brand", {"id": "B001", "name": "Brand1"})
        entity = ctx.get_entity("brand", "B001")
        assert entity is not None
        assert entity["name"] == "Brand1"
        assert ctx.get_entity("brand", "nonexistent") is None

    def test_sample_from_registered(self, ctx, tiny_profile):
        # Register some brands
        for i in range(5):
            ctx.register("brand", {"id": f"B{i:03d}", "name": f"Brand{i}"})
        sampled = ctx.sample("brand", 2)
        assert len(sampled) == 2
        assert all("name" in s for s in sampled)

    def test_sample_empty_raises(self, ctx):
        with pytest.raises(ValueError, match="尚未注册"):
            ctx.sample("nonexistent")

    def test_count(self, ctx):
        assert ctx.count("brand") == 0
        ctx.register("brand", {"id": "B001"})
        assert ctx.count("brand") == 1

    def test_reproducible_with_same_seed(self, tiny_profile):
        ctx1 = GenerationContext(tiny_profile, seed=42)
        ctx2 = GenerationContext(tiny_profile, seed=42)

        # Generate IDs with both contexts
        ids1 = [ctx1.next_id("test", "T") for _ in range(10)]
        ids2 = [ctx2.next_id("test", "T") for _ in range(10)]
        assert ids1 == ids2

    def test_different_seeds_produce_different_faker_output(self, tiny_profile):
        """不同 seed 的 Faker 产出应该不同（next_id 是计数器，不受 seed 影响）。"""
        ctx1 = GenerationContext(tiny_profile, seed=42)
        ctx2 = GenerationContext(tiny_profile, seed=99)

        # Faker 受 seed 影响
        name1 = ctx1.faker.name()
        name2 = ctx2.faker.name()
        assert name1 != name2

    def test_same_seeds_produce_same_faker_output(self, tiny_profile):
        """相同 seed 的 Faker 产出应该一致。"""
        ctx1 = GenerationContext(tiny_profile, seed=42)
        ctx2 = GenerationContext(tiny_profile, seed=42)
        assert ctx1.faker.name() == ctx2.faker.name()

    def test_resolve_ref_by_index(self, ctx):
        ctx.register("brand", {"id": "B001", "name": "Brand1"})
        ctx.register("brand", {"id": "B002", "name": "Brand2"})
        ctx.register("brand", {"id": "B003", "name": "Brand3"})

        ref = "$ref:brand:1"
        result = ctx.resolve_ref(ref)
        assert result == "B002"

    def test_resolve_ref_by_key(self, ctx):
        ctx.register("brand", {"id": "B001", "name": "Brand1"})
        ref = "$ref:brand:B001"
        result = ctx.resolve_ref(ref)
        assert result == "B001"

    def test_resolve_ref_non_ref_passthrough(self, ctx):
        assert ctx.resolve_ref("plain_value") == "plain_value"

    def test_resolve_ref_invalid_entity(self, ctx):
        with pytest.raises(ValueError, match="尚未注册"):
            ctx.resolve_ref("$ref:nonexistent:0")

    def test_summary(self, ctx):
        ctx.register("brand", {"id": "B001"})
        ctx.register("brand", {"id": "B002"})
        ctx.register("category", {"id": "C001"})
        summary = ctx.summary()
        assert summary == {"brand": 2, "category": 1}

    def test_reset_counter(self, ctx):
        _ = ctx.next_id("test", "T")
        _ = ctx.next_id("test", "T")
        assert ctx.next_id("test", "T") == "T0003"
        ctx.reset_counter("test")
        assert ctx.next_id("test", "T") == "T0001"


# ======================= TestValidators =======================

class TestReferentialValidator:
    def test_passes_with_valid_data(self, ctx):
        ctx.register("brand", {"id": "B001", "name": "Test"})
        ctx.register("product", {"product_id": "P001", "brand_id": "B001"})

        # Register FK mapping
        validator = ReferentialValidator()
        validator.register_fk("product", "brand_id", "brand")

        result = validator.validate(ctx)
        assert result.is_valid

    def test_catches_broken_fk(self, ctx):
        ctx.register("brand", {"id": "B001", "name": "Test"})
        ctx.register("product", {"product_id": "P001", "brand_id": "B999"})

        validator = ReferentialValidator()
        validator.register_fk("product", "brand_id", "brand")

        result = validator.validate(ctx)
        assert not result.is_valid
        assert len(result.errors) >= 1


class TestVolumeValidator:
    def test_passes_exact_match(self, tiny_profile):
        ctx = GenerationContext(tiny_profile, seed=42)
        ctx.register_batch("brand", [{"id": f"B{i}"} for i in range(3)])

        validator = VolumeValidator()
        result = validator.validate(ctx)
        assert result.is_valid

    def test_detects_mismatch(self, tiny_profile):
        ctx = GenerationContext(tiny_profile, seed=42)
        ctx.register_batch("brand", [{"id": f"B{i}"} for i in range(10)])  # expected 3

        validator = VolumeValidator()
        result = validator.validate(ctx)
        assert not result.is_valid

    def test_tolerance_allows_deviation(self, tiny_profile):
        ctx = GenerationContext(tiny_profile, seed=42)
        ctx.register_batch("brand", [{"id": f"B{i}"} for i in range(4)])  # expected 3

        validator = VolumeValidator(tolerance=0.5)  # 50% tolerance
        result = validator.validate(ctx)
        assert result.is_valid  # 4 vs 3 is 33% deviation < 50%

    def test_strict_tolerance_rejects(self, tiny_profile):
        ctx = GenerationContext(tiny_profile, seed=42)
        ctx.register_batch("brand", [{"id": f"B{i}"} for i in range(10)])

        validator = VolumeValidator(tolerance=0.5)  # 50% tolerance
        result = validator.validate(ctx)
        assert not result.is_valid  # 10 vs 3 is 233% deviation

    def test_skips_unconfigured_entities(self, tiny_profile):
        ctx = GenerationContext(tiny_profile, seed=42)
        ctx.register_batch("unknown_entity", [{"id": "X001"}])

        validator = VolumeValidator()
        result = validator.validate(ctx)
        assert result.is_valid  # unknown_entity not in profile, skip


# ======================= TestExporters =======================

class TestDictExporter:
    def test_export_all_entities(self, ctx):
        ctx.register("brand", {"id": "B001", "name": "Brand1"})
        ctx.register("brand", {"id": "B002", "name": "Brand2"})

        exporter = DictExporter()
        data = exporter.export(ctx)
        assert "brand" in data
        assert len(data["brand"]) == 2

    def test_export_empty_context(self, ctx):
        exporter = DictExporter()
        data = exporter.export(ctx)
        assert data == {}

    def test_export_summary(self, ctx):
        ctx.register("brand", {"id": "B001"})
        exporter = DictExporter()
        summary = exporter.export_summary(ctx)
        assert summary["profile"] == "tiny"
        assert summary["entity_counts"]["brand"] == 1


class TestJsonFileExporter:
    def test_export_to_temp_dir(self, ctx):
        ctx.register("brand", {"id": "B001", "name": "TestBrand"})

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = JsonFileExporter(tmpdir)
            exporter.export(ctx)

            # Check JSON file exists
            brand_file = Path(tmpdir) / "brand.json"
            assert brand_file.exists()

            content = json.loads(brand_file.read_text(encoding="utf-8"))
            assert len(content) == 1
            assert content[0]["name"] == "TestBrand"

            # Check summary exists
            summary_file = Path(tmpdir) / "_summary.json"
            assert summary_file.exists()


# ======================= TestSeedValidator =======================

class TestSeedValidator:
    def test_validate_all(self, tiny_profile):
        ctx = GenerationContext(tiny_profile, seed=42)
        ctx.register_batch("brand", [{"id": f"B{i}"} for i in range(3)])

        sv = SeedValidator([
            ReferentialValidator(),
            VolumeValidator(),
        ])
        results = sv.validate_all(ctx)
        assert len(results) == 2
        assert all(r.is_valid for r in results)


# ======================= TestValidationResult =======================

class TestValidationResult:
    def test_is_valid(self):
        r = ValidationResult(validator_name="test")
        assert r.is_valid is True

    def test_with_errors(self):
        r = ValidationResult(validator_name="test", errors=["bad"])
        assert r.is_valid is False

    def test_has_warnings(self):
        r = ValidationResult(validator_name="test", warnings=["hmm"])
        assert r.has_warnings is True
        assert r.is_valid is True  # Warnings don't invalidate
